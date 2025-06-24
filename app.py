from flask import Flask, render_template, request, jsonify, send_file
import os
import platform
import requests
import tempfile
import shutil
from pdf2image import convert_from_path
import re
import openpyxl
from werkzeug.utils import secure_filename
import logging
from functools import wraps

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration with better error handling
try:
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'uploads')
    OUTPUT_FOLDER = os.environ.get('OUTPUT_FOLDER', 'output')
    
    # Create directories with proper error handling
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    os.makedirs('pages', exist_ok=True)
    
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB limit
    
    logger.info(f"Upload folder: {UPLOAD_FOLDER}")
    logger.info(f"Output folder: {OUTPUT_FOLDER}")
    
except Exception as e:
    logger.error(f"Failed to create directories: {e}")

# Dynamic Poppler Path with better detection
def get_poppler_path():
    if platform.system() == "Windows":
        possible_paths = [
            os.getenv('POPPLER_PATH'),
            r"C:\poppler-24.08.0\Library\bin",
            r"C:\Program Files\poppler\bin",
            r"C:\poppler\bin"
        ]
        for path in possible_paths:
            if path and os.path.exists(path):
                return path
        return None
    else:
        # On Linux/Mac, check if poppler-utils is installed
        import subprocess
        try:
            subprocess.run(['pdftoppm', '--help'], 
                         stdout=subprocess.DEVNULL, 
                         stderr=subprocess.DEVNULL, 
                         check=True)
            return None  # System-wide installation
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.warning("Poppler not found in system PATH")
            return None

POPPLER_PATH = get_poppler_path()
logger.info(f"Poppler path: {POPPLER_PATH}")

# API Key with validation
API_KEY = os.getenv('OCR_SPACE_API_KEY', 'K84473361288957')
if not API_KEY or API_KEY == 'your_api_key_here':
    logger.warning("OCR.Space API key not properly configured")

def handle_errors(f):
    """Decorator to handle errors and return proper JSON responses"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}")
            return jsonify({
                'error': f'Internal server error: {str(e)}',
                'success': False
            }), 500
    return decorated_function

@app.errorhandler(404)
def not_found_error(error):
    return jsonify({'error': 'Endpoint not found', 'success': False}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error', 'success': False}), 500

@app.errorhandler(413)
def too_large(error):
    return jsonify({'error': 'File too large. Maximum size is 16MB', 'success': False}), 413

@app.route('/')
def index():
    try:
        return render_template('index.html')
    except Exception as e:
        logger.error(f"Template error: {e}")
        return jsonify({
            'error': 'Template not found. Please ensure index.html exists in templates folder.',
            'success': False
        }), 500

@app.route('/health')
def health_check():
    """Health check endpoint for deployment platforms"""
    return jsonify({
        'status': 'healthy',
        'poppler_available': POPPLER_PATH is not None or platform.system() != "Windows",
        'directories_created': all([
            os.path.exists(UPLOAD_FOLDER),
            os.path.exists(OUTPUT_FOLDER),
            os.path.exists('pages')
        ])
    })

@app.route('/upload', methods=['POST'])
@handle_errors
def upload_file():
    # Enhanced validation
    if 'file' not in request.files:
        return jsonify({'error': 'No file part in request', 'success': False}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected', 'success': False}), 400

    if not file.filename.lower().endswith('.pdf'):
        return jsonify({
            'error': 'Invalid file type. Only PDF files are allowed.',
            'success': False
        }), 400

    # Secure filename handling
    filename = secure_filename(file.filename)
    if not filename:
        filename = 'uploaded_file.pdf'
    
    pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    try:
        file.save(pdf_path)
        logger.info(f"File saved to: {pdf_path}")
        
        # Verify file was saved
        if not os.path.exists(pdf_path):
            raise Exception("Failed to save uploaded file")
        
        result = process_pdf(pdf_path)
        result['success'] = True
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Upload processing error: {e}")
        return jsonify({
            'error': f'Failed to process PDF: {str(e)}',
            'success': False
        }), 500
    finally:
        # Clean up uploaded file
        try:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
        except Exception as e:
            logger.warning(f"Failed to clean up uploaded file: {e}")

def process_pdf(pdf_path):
    """Enhanced PDF processing with better error handling"""
    try:
        # Convert PDF to images with error handling
        logger.info(f"Converting PDF: {pdf_path}")
        
        convert_kwargs = {}
        if POPPLER_PATH:
            convert_kwargs['poppler_path'] = POPPLER_PATH
        
        images = convert_from_path(pdf_path, **convert_kwargs)
        logger.info(f"PDF split into {len(images)} page(s)")
        
        if not images:
            raise Exception("No pages found in PDF")

    except Exception as e:
        logger.error(f"PDF conversion error: {e}")
        raise Exception(f"Failed to convert PDF to images: {str(e)}")

    full_extracted_text = ""
    successful_pages = 0

    for i, img in enumerate(images):
        try:
            # Use temporary file for better cleanup
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_file:
                image_path = temp_file.name
                img.save(image_path, "JPEG")

            logger.info(f"Processing page {i+1}/{len(images)}")

            # OCR processing with enhanced error handling
            page_text = extract_text_from_image(image_path, i+1)
            if page_text and page_text.strip():
                full_extracted_text += f"\n--- Page {i+1} ---\n" + page_text
                successful_pages += 1
            
        except Exception as e:
            logger.error(f"Error processing page {i+1}: {e}")
            continue
        finally:
            # Clean up temporary image file
            try:
                if os.path.exists(image_path):
                    os.remove(image_path)
            except Exception as e:
                logger.warning(f"Failed to clean up image file: {e}")

    if successful_pages == 0:
        raise Exception("No text could be extracted from any page of the PDF")

    logger.info(f"Successfully processed {successful_pages}/{len(images)} pages")

    # Save extracted text
    text_output_path = os.path.join(app.config['OUTPUT_FOLDER'], 'final_extracted_text.txt')
    with open(text_output_path, "w", encoding="utf-8") as f:
        f.write(full_extracted_text)

    # Extract structured data
    extracted_data = extract_structured_data(full_extracted_text)

    # Create Excel file
    excel_output_path = create_excel_file(extracted_data)

    return {
        'message': f'File processed successfully. {successful_pages}/{len(images)} pages processed.',
        'extracted_data': extracted_data,
        'text_file': 'final_extracted_text.txt',
        'excel_file': 'inspection_data.xlsx',
        'pages_processed': successful_pages,
        'total_pages': len(images)
    }

def extract_text_from_image(image_path, page_num):
    """Extract text from image using OCR.space API"""
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            with open(image_path, 'rb') as f:
                response = requests.post(
                    'https://api.ocr.space/parse/image',
                    files={'filename': f},
                    data={
                        'apikey': API_KEY,
                        'language': 'eng',
                        'isOverlayRequired': False,
                        'OCREngine': 2
                    },
                    timeout=60
                )

            if response.status_code == 200:
                content_type = response.headers.get('Content-Type', '')
                if 'application/json' in content_type:
                    result = response.json()

                    if result.get('IsErroredOnProcessing'):
                        error_msg = result.get('ErrorMessage', 'Unknown API error')
                        logger.error(f"API Error on page {page_num}: {error_msg}")
                        return ""

                    if result.get('ParsedResults') and len(result['ParsedResults']) > 0:
                        page_text = result['ParsedResults'][0]['ParsedText']
                        logger.info(f"Text extracted from page {page_num}")
                        return page_text
                    else:
                        logger.warning(f"No parsed results for page {page_num}")
                        return ""
                else:
                    logger.error(f"Non-JSON response on page {page_num}: {response.text[:200]}")
                    return ""
            else:
                logger.error(f"HTTP Error {response.status_code} on page {page_num}")
                if retry_count < max_retries - 1:
                    retry_count += 1
                    logger.info(f"Retrying page {page_num} (attempt {retry_count + 1})")
                    continue
                return ""

        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for page {page_num}: {e}")
            if retry_count < max_retries - 1:
                retry_count += 1
                logger.info(f"Retrying page {page_num} (attempt {retry_count + 1})")
                continue
            return ""
        except Exception as e:
            logger.error(f"Unexpected error processing page {page_num}: {e}")
            return ""
        
        break  # Success, exit retry loop
    
    return ""

def extract_structured_data(text):
    """Extract structured fields using regex patterns"""
    patterns = {
        "RFI NO": r"RFI\s*NO[:\s]*([^\n\r]+)",
        "Date of Inspection": r"Date\s*of\s*Inspection[:\s]*([^\n\r]+)",
        "Description of work": r"Description\s*of\s*work[:\s]*([^\n\r]+)",
        "Location": r"Location[:\s]*([^\n\r]+)",
        "Material source": r"Material\s*source[:\s]*([^\n\r]+)",
        "Width": r"Width[:\s]*([^\n\r]+)"
    }

    extracted_data = {}
    for field, pattern in patterns.items():
        try:
            match = re.search(pattern, text, re.IGNORECASE)
            extracted_data[field] = match.group(1).strip() if match else "Not Found"
        except Exception as e:
            logger.error(f"Error extracting {field}: {e}")
            extracted_data[field] = "Error"

    return extracted_data

def create_excel_file(extracted_data):
    """Create Excel file from extracted data"""
    excel_output_path = os.path.join(app.config['OUTPUT_FOLDER'], 'inspection_data.xlsx')
    
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Inspection Data"
        
        # Add headers and data
        headers = list(extracted_data.keys())
        values = list(extracted_data.values())
        
        ws.append(headers)
        ws.append(values)
        
        # Auto-adjust column widths
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width
        
        wb.save(excel_output_path)
        logger.info(f"Excel file created: {excel_output_path}")
        return excel_output_path
        
    except Exception as e:
        logger.error(f"Error creating Excel file: {e}")
        raise Exception(f"Failed to create Excel file: {str(e)}")

@app.route('/download/<filename>')
@handle_errors
def download_file(filename):
    # Security: only allow downloading from output folder
    safe_filename = secure_filename(filename)
    file_path = os.path.join(app.config['OUTPUT_FOLDER'], safe_filename)
    
    if os.path.exists(file_path):
        try:
            return send_file(file_path, as_attachment=True)
        except Exception as e:
            logger.error(f"Error sending file {filename}: {e}")
            return jsonify({'error': 'Failed to send file', 'success': False}), 500
    else:
        return jsonify({'error': 'File not found', 'success': False}), 404

# Additional endpoint for debugging
@app.route('/debug')
def debug_info():
    """Debug endpoint to check system status"""
    return jsonify({
        'platform': platform.system(),
        'poppler_path': POPPLER_PATH,
        'upload_folder_exists': os.path.exists(UPLOAD_FOLDER),
        'output_folder_exists': os.path.exists(OUTPUT_FOLDER),
        'api_key_configured': bool(API_KEY and API_KEY != 'your_api_key_here'),
        'python_version': platform.python_version()
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    logger.info(f"Starting Flask app on port {port}")
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
