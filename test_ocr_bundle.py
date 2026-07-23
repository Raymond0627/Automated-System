import pytesseract
import cv2
import sys
import os

tesseract_path = r"C:\Users\Lumeed\Desktop\Lumeed\Customer\IC\VS code\Opencode\Automated System\dist\LumeedQScan\tesseract\tesseract.exe"
pytesseract.tesseract_cmd = tesseract_path
os.environ["TESSDATA_PREFIX"] = r"C:\Users\Lumeed\Desktop\Lumeed\Customer\IC\VS code\Opencode\Automated System\dist\LumeedQScan\tesseract\tessdata"

img = cv2.imread("test_ocr.png")
if img is None:
    print("ERROR: Could not read test image")
    sys.exit(1)

text = pytesseract.image_to_string(img, config="--psm 6")
print(f"OCR text: [{text.strip()}]")

data = pytesseract.image_to_data(img, config="--psm 6", output_type=pytesseract.Output.DICT)
confs = [c for c in data["conf"] if c > 0]
print(f"Confidences: {confs}")
print(f"Words: {[t for t in data['text'] if t.strip()]}")
