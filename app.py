from flask import Flask, render_template, request, jsonify, send_file
import os
import platform
import requests
from pdf2image import convert_from_path
import re
import openpyxl
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'output'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB limit

# Dynamic Poppler Path (✅ Windows vs. Linux compatible)
if platform.system() == "Windows":
    POPPLER_PATH = os.getenv('POPPLER_PATH', r"C:\poppler-24.08.0\Library\bin")  # Default Windows path or from environment
else:
    POPPLER_PATH = None  # On Linux/Mac (deployment), Poppler is system-wide

# Dynamic API Key (✅ Secure for deployment)
API_KEY = os.getenv('OCR_SPACE_API_KEY', 'K81797551788957')  # Default or from environment

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file and file.filename.lower().endswith('.pdf'):
        filename = secure_filename(file.filename)
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(pdf_path)

        try:
            result = process_pdf(pdf_path)
            return jsonify(result)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        return jsonify({'error': 'Invalid file type. Only PDF files are allowed.'}), 400

def process_pdf(pdf_path):
    # === STEP 1: Convert PDF to images ===
    images = convert_from_path(pdf_path, poppler_path=POPPLER_PATH)
    print(f"✅ PDF split into {len(images)} page(s).")

    os.makedirs("pages", exist_ok=True)
    full_extracted_text = ""

    for i, img in enumerate(images):
        image_path = f"pages/page_{i+1}.jpg"
        img.save(image_path, "JPEG")

        print(f"\n📄 Uploading page {i+1} to OCR.space...")

        with open(image_path, 'rb') as f:
            response = requests.post(
                'https://api.ocr.space/parse/image',
                files={'filename': f},
                data={
                    'apikey': API_KEY,
                    'language': 'eng',
                    'isOverlayRequired': False,
                    'OCREngine': 2
                }
            )

        result = response.json()
        try:
            page_text = result['ParsedResults'][0]['ParsedText']
            full_extracted_text += f"\n--- Page {i+1} ---\n" + page_text
            print(f"✅ Text extracted from page {i+1}")
        except Exception as e:
            print("❌ Error extracting text from page", i+1, ":", result)
            raise Exception(f"Failed to extract text from page {i+1}: {str(result)}")

    # === STEP 3: Save extracted raw text ===
    text_output_path = os.path.join(app.config['OUTPUT_FOLDER'], 'final_extracted_text.txt')
    with open(text_output_path, "w", encoding="utf-8") as f:
        f.write(full_extracted_text)

    # === STEP 4: Extract structured fields using REGEX ===
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
        match = re.search(pattern, full_extracted_text, re.IGNORECASE)
        extracted_data[field] = match.group(1).strip() if match else "Not Found"

    # === STEP 5: Save data to Excel ===
    excel_output_path = os.path.join(app.config['OUTPUT_FOLDER'], 'inspection_data.xlsx')
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inspection Data"
    ws.append(list(extracted_data.keys()))     # Header row
    ws.append(list(extracted_data.values()))   # Data row
    wb.save(excel_output_path)

    return {
        'message': 'File processed successfully',
        'extracted_data': extracted_data,
        'text_file': text_output_path,
        'excel_file': excel_output_path
    }

@app.route('/download/<filename>')
def download_file(filename):
    file_path = os.path.join(app.config['OUTPUT_FOLDER'], filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    else:
        return jsonify({'error': 'File not found'}), 404

if __name__ == '__main__':
    app.run(debug=True)
