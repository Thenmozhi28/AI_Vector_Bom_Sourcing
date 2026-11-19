import pandas as pd
from fpdf import FPDF
import os

uploads_dir = "uploads"
os.makedirs(uploads_dir, exist_ok=True)

# 1. Create a "Human-Mindset" Messy Excel File
print("Generating messy Excel file...")
excel_path = os.path.join(uploads_dir, "human_messy_bom.xlsx")

# Create data with unstructured and weirdly formatted rows
data = [
    ["PROJECT ALPHA - INTERNAL PROCUREMENT LIST", "", "", ""],
    ["Date: 2026-05-17", "Requested by: Engineering Team", "", ""],
    ["Please order the following ASAP.", "", "", ""],
    ["", "", "", ""],
    ["Item Ref #", "Component Details & Specifications", "Req. Qty", "Vendor/Brand Info"],
    ["1", "Resistor 10k Ohm 5% 0.25W", "1000", "YAGEO RC0402FR-0710KL"], # MPN in brand column
    ["2", "3VA5180-5ED11-1AA0 / Siemens / Circuit Breaker", "5", ""], # Delimited string
    ["Replacement Sensor", "WT2F-P170", "2", "SICK"], # MPN in details, label in item ref
    ["Supplier Note:", "Need 3VA5180-5ED11-1AA0", "", ""], # Conversational text with MPN
    ["", "", "", ""],
    ["5", "SICK Energetic Photoelectric Sensor", "10", "WT2F-P450"], # MPN in Vendor column
    ["Motion IC", "TLV493DA2BWXTMA1", "50", "Infineon"]
]

df = pd.DataFrame(data)
df.to_excel(excel_path, index=False, header=False)
print(f"Excel file created at: {excel_path}")

# 2. Create a PDF BOM File
print("Generating PDF BOM file...")
pdf_path = os.path.join(uploads_dir, "procurement_bom.pdf")

pdf = FPDF()
pdf.add_page()
pdf.set_font("Arial", "B", 16)
pdf.cell(200, 10, txt="Procurement Bill of Materials (BOM)", ln=True, align="C")

pdf.set_font("Arial", "", 12)
pdf.cell(200, 10, txt="Date: May 17, 2026", ln=True)
pdf.cell(200, 10, txt="Project: Robotics Vision System v2", ln=True)
pdf.cell(200, 10, txt="-"*50, ln=True)

# Add table header
pdf.set_font("Arial", "B", 10)
pdf.cell(20, 10, "Item")
pdf.cell(50, 10, "Part Number")
pdf.cell(80, 10, "Description")
pdf.cell(20, 10, "Qty")
pdf.ln()

# Add BOM rows
pdf.set_font("Arial", "", 10)

# Row 1
pdf.cell(20, 10, "1")
pdf.cell(50, 10, "WT2F-P170")
pdf.cell(80, 10, "SICK Energetic Photoelectric Sensor")
pdf.cell(20, 10, "15")
pdf.ln()

# Row 2
pdf.cell(20, 10, "2")
pdf.cell(50, 10, "3VA5180-5ED11-1AA0")
pdf.cell(80, 10, "Siemens MCCB Circuit Breaker")
pdf.cell(20, 10, "4")
pdf.ln()

# Row 3
pdf.cell(20, 10, "3")
pdf.cell(50, 10, "TLV493DA2BWXTMA1")
pdf.cell(80, 10, "Infineon 3-Axis Magnetic Sensor")
pdf.cell(20, 10, "100")
pdf.ln()

# Add a messy conversational note at the bottom
pdf.ln(10)
pdf.set_font("Arial", "I", 10)
pdf.multi_cell(0, 10, "Note to Procurement: Please make sure we also stock up on WT2F-P450 as a backup for Item 1 if lead times are too long.")

pdf.output(pdf_path)
print(f"PDF file created at: {pdf_path}")
