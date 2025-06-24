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

# Dynamic Poppler Path
if platform.system() == "Windows":
    POPPLER_PATH = os.getenv('POPPLER_PATH', r"C:\poppler-24.08.0\Library\bin")
else:
    POPPLER_PATH = None  # On Linux/Mac, Poppler is system-wide

# Dynamic API Key
API_KEY = os.getenv('OCR_SPACE_API_KEY', 'K84473361288957')

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

        # ✅ Check HTTP status code before parsing JSON
        if response.status_code == 200:
            try:
                result = response.json()

                if result.get('IsErroredOnProcessing'):
                    print(f"❌ API Error on page {i+1}: {result.get('ErrorMessage')}")
                    continue

                page_text = result['ParsedResults'][0]['ParsedText']
                full_extracted_text += f"\n--- Page {i+1} ---\n" + page_text
                print(f"✅ Text extracted from page {i+1}")
            except Exception as e:
                print(f"❌ JSON parsing error on page {i+1}: {e}")
                print("Raw Response:", response.text)
                continue
        else:
            print(f"❌ HTTP Error: {response.status_code} on page {i+1}")
            print("Raw Response:", response.text)
            continue

    if not full_extracted_text.strip():
        raise Exception("No text could be extracted from the PDF.")

    text_output_path = os.path.join(app.config['OUTPUT_FOLDER'], 'final_extracted_text.txt')
    with open(text_output_path, "w", encoding="utf-8") as f:
        f.write(full_extracted_text)

    # Extract structured fields using REGEX
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

    excel_output_path = os.path.join(app.config['OUTPUT_FOLDER'], 'inspection_data.xlsx')
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inspection Data"
    ws.append(list(extracted_data.keys()))
    ws.append(list(extracted_data.values()))
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
    app.run(host='0.0.0.0', port=10000)
