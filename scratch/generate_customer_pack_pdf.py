#!/usr/bin/env python3
import os
import math
from fpdf import FPDF

# Robust text cleaner to normalize special characters for FPDF core fonts (latin-1)
def clean_text(text):
    if not text:
        return ""
    replacements = {
        "\u2014": " - ",  # em-dash
        "\u2013": "-",    # en-dash
        "\u201c": '"',    # left double curly quote
        "\u201d": '"',    # right double curly quote
        "\u2018": "'",    # left single curly quote
        "\u2019": "'",    # right single curly quote
        "\u2022": "*",    # bullet point
        "\u2122": "(TM)", # trademark
        "\u00ae": "(R)",  # registered
        "\u00a9": "(C)",  # copyright
    }
    for orig, rep in replacements.items():
        text = text.replace(orig, rep)
    # Fallback to safely handle any unmappable characters
    return text.encode('latin-1', 'replace').decode('latin-1')

class CustomerPackPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            # Thin top rule
            self.set_draw_color(226, 232, 240)
            self.set_line_width(0.4)
            self.line(self.l_margin, 18, self.w - self.r_margin, 18)
            
            # Header text
            self.set_y(10)
            self.set_font('Helvetica', 'B', 7.5)
            self.set_text_color(148, 163, 184) # slate gray
            self.cell(0, 8, clean_text("RS GROUP PLC  |  CUSTOMER ENGAGEMENT & INTERACTION PACK"), align="L", new_x="LMARGIN", new_y="NEXT")
            self.ln(3)

    def footer(self):
        if self.page_no() > 1:
            # Thin bottom rule
            self.set_draw_color(226, 232, 240)
            self.set_line_width(0.4)
            self.line(self.l_margin, self.h - 18, self.w - self.r_margin, self.h - 18)
            
            # Footer text
            self.set_y(-15)
            self.set_font('Helvetica', '', 8)
            self.set_text_color(148, 163, 184)
            
            # Left aligned: Confidential
            self.cell(0, 10, clean_text("CONFIDENTIAL  -  FOR INTERNAL USE ONLY"), align="L", new_x="LMARGIN", new_y="LAST")
            # Right aligned: Page numbers
            self.cell(0, 10, f"Page {self.page_no()} of {self.pages_count}", align="R")

def draw_cover_page(pdf):
    pdf.add_page()
    # Deep slate banner
    pdf.set_fill_color(30, 41, 59) # Deep Slate Slate Navy
    pdf.rect(0, 0, 210, 125, style="F")
    
    # Royal Blue strip
    pdf.set_fill_color(37, 99, 235) # Royal Blue Accent
    pdf.rect(0, 125, 210, 6, style="F")
    
    # Cover title
    pdf.set_y(40)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(37, 99, 235) # Accent Blue
    pdf.cell(0, 6, clean_text("TECHNICAL INTERACTION & CUSTOMER ENGAGEMENT"), align="L", new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(3)
    pdf.set_font('Helvetica', 'B', 25)
    pdf.set_text_color(255, 255, 255) # White
    pdf.multi_cell(0, 11, clean_text("CUSTOMER ENGAGEMENT\n& INTERACTION PACK"), align="L", new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(5)
    pdf.set_font('Helvetica', 'I', 12.5)
    pdf.set_text_color(226, 232, 240)
    pdf.cell(0, 8, clean_text("Discovery Agenda, Live PoC Guide, and Workshop Integration Framework"), align="L", new_x="LMARGIN", new_y="NEXT")
    
    # Bottom metadata
    pdf.set_y(155)
    pdf.set_fill_color(30, 41, 59)
    pdf.rect(20, 155, 2, 70, style="F")
    
    pdf.set_x(26)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(0, 5, clean_text("PREPARED FOR"), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(26)
    pdf.set_font('Helvetica', 'B', 12)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 6, clean_text("RS Group plc Procurement & Supply Chain Technology Leadership"), align="L", new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(5)
    pdf.set_x(26)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(0, 5, clean_text("CONTEXT & TIMELINE"), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(26)
    pdf.set_font('Helvetica', 'B', 12)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 6, clean_text("Next Steps as per the AI Vector BOM Sourcing Enterprise Proposal"), align="L", new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(5)
    pdf.set_x(26)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(0, 5, clean_text("DATE & VERSION"), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(26)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 6, clean_text("May 2026  |  v1.0 (Production Deliverable Pack)"), align="L", new_x="LMARGIN", new_y="NEXT")

def add_section_header(pdf, title):
    pdf.ln(5)
    pdf.set_font('Helvetica', 'B', 14)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 8, clean_text(title), new_x="LMARGIN", new_y="NEXT")
    
    # Royal Blue accent line
    pdf.set_draw_color(37, 99, 235)
    pdf.set_line_width(1.0)
    pdf.line(pdf.l_margin, pdf.get_y() - 1, pdf.w - pdf.r_margin, pdf.get_y() - 1)
    pdf.ln(4)

def add_subsection_header(pdf, title):
    pdf.ln(3)
    x = pdf.get_x()
    y = pdf.get_y()
    pdf.set_fill_color(37, 99, 235)
    pdf.rect(x, y + 1.2, 2.5, 7.5, style="F")
    
    pdf.set_x(x + 5)
    pdf.set_font('Helvetica', 'B', 10.5)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 9, clean_text(title), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

def add_styled_paragraph(pdf, text):
    text = clean_text(text)
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(51, 65, 85)
    
    parts = text.split("**")
    is_bold = False
    for part in parts:
        if is_bold:
            pdf.set_font('Helvetica', 'B', 10)
        else:
            pdf.set_font('Helvetica', '', 10)
        pdf.write(5.8, part)
        is_bold = not is_bold
    pdf.ln(7.5)

def add_callout_box(pdf, text, title="DELIVERABLE SUMMARY"):
    text = clean_text(text)
    title = clean_text(title)
    pdf.ln(2)
    y_start = pdf.get_y()
    
    # Estimate height
    char_limit = 82
    lines = 0
    for paragraph in text.split('\n'):
        lines += max(1, math.ceil(len(paragraph) / char_limit))
    
    height = lines * 5.8 + 14
    
    # Draw background panel
    pdf.set_fill_color(248, 250, 252) # Light slate neutral
    pdf.rect(20, y_start, 170, height, style="F")
    
    # Left blue border
    pdf.set_fill_color(37, 99, 235)
    pdf.rect(20, y_start, 2.5, height, style="F")
    
    pdf.set_y(y_start + 4)
    pdf.set_x(26)
    pdf.set_left_margin(26)
    pdf.set_right_margin(24)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(37, 99, 235)
    pdf.cell(0, 5, title, new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_font('Helvetica', 'I', 10)
    pdf.set_text_color(30, 41, 59)
    pdf.multi_cell(0, 5.5, text, new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_left_margin(20)
    pdf.set_right_margin(20)
    pdf.set_x(20)
    pdf.set_y(y_start + height + 4)

def add_bullet_point(pdf, num_text, body_text):
    num_text = clean_text(num_text)
    body_text = clean_text(body_text)
    
    pdf.ln(1)
    y = pdf.get_y()
    
    # Number bubble
    pdf.set_fill_color(241, 245, 249)
    pdf.rect(20, y + 0.5, 5, 5, style="F")
    pdf.set_draw_color(203, 213, 225)
    pdf.set_line_width(0.3)
    pdf.rect(20, y + 0.5, 5, 5, style="D")
    
    # Draw text inside bubble
    pdf.set_y(y + 0.5)
    pdf.set_x(20)
    pdf.set_font('Helvetica', 'B', 8.5)
    pdf.set_text_color(37, 99, 235)
    pdf.cell(5, 5, num_text, align="C", new_x="LMARGIN", new_y="LAST")
    
    # Bullet Text
    pdf.set_left_margin(27)
    pdf.set_x(27)
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(51, 65, 85)
    
    # Parse bold split
    parts = body_text.split("**")
    is_bold = False
    for part in parts:
        if is_bold:
            pdf.set_font('Helvetica', 'B', 10)
        else:
            pdf.set_font('Helvetica', '', 10)
        pdf.write(5.5, part)
        is_bold = not is_bold
    pdf.ln(7)
    
    pdf.set_left_margin(20)
    pdf.set_x(20)

def add_question_box(pdf, q_num, question, hint):
    q_num = clean_text(q_num)
    question = clean_text(question)
    hint = clean_text(hint)
    
    pdf.ln(2)
    y_start = pdf.get_y()
    
    # Estimate height
    char_limit = 80
    lines = max(1, math.ceil(len(question) / char_limit)) + max(1, math.ceil(len(hint) / char_limit))
    height = lines * 5.5 + 12
    
    # Background panel
    pdf.set_fill_color(255, 255, 255)
    pdf.rect(20, y_start, 170, height, style="F")
    # Border
    pdf.set_draw_color(226, 232, 240)
    pdf.set_line_width(0.5)
    pdf.rect(20, y_start, 170, height, style="D")
    # Left colored accent tag
    pdf.set_fill_color(37, 99, 235)
    pdf.rect(20, y_start, 2, height, style="F")
    
    pdf.set_y(y_start + 3)
    pdf.set_x(25)
    pdf.set_left_margin(25)
    pdf.set_right_margin(25)
    
    # Q Title
    pdf.set_font('Helvetica', 'B', 9.5)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 5, f"{q_num}: {question}", new_x="LMARGIN", new_y="NEXT")
    
    # Hint Content
    pdf.ln(1)
    pdf.set_font('Helvetica', 'I', 9)
    pdf.set_text_color(100, 116, 139)
    pdf.multi_cell(0, 4.5, hint, new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_left_margin(20)
    pdf.set_right_margin(20)
    pdf.set_x(20)
    pdf.set_y(y_start + height + 3)

def draw_poc_scenario_card(pdf, num, name, input_str, action_str, result_str):
    num = clean_text(num)
    name = clean_text(name)
    input_str = clean_text(input_str)
    action_str = clean_text(action_str)
    result_str = clean_text(result_str)
    
    pdf.ln(2)
    y_start = pdf.get_y()
    
    # Background card
    pdf.set_fill_color(248, 250, 252) # light gray
    pdf.rect(20, y_start, 170, 44, style="F")
    # Left border
    pdf.set_fill_color(37, 99, 235)
    pdf.rect(20, y_start, 2, 44, style="F")
    
    pdf.set_y(y_start + 3)
    pdf.set_x(25)
    pdf.set_left_margin(25)
    
    # Header: Scenario X
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(37, 99, 235)
    pdf.cell(0, 5, f"Scenario {num}: {name}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.5)
    
    # Customer Input line
    pdf.set_font('Helvetica', 'B', 8.5)
    pdf.set_text_color(100, 116, 139)
    pdf.write(4.5, "CUSTOMER INPUT: ")
    pdf.set_font('Helvetica', '', 8.5)
    pdf.set_text_color(30, 41, 59)
    pdf.write(4.5, f"{input_str}\n")
    
    # AI Action line
    pdf.set_font('Helvetica', 'B', 8.5)
    pdf.set_text_color(100, 116, 139)
    pdf.write(4.5, "THE AI ACTION: ")
    pdf.set_font('Helvetica', '', 8.5)
    pdf.set_text_color(30, 41, 59)
    pdf.write(4.5, f"{action_str}\n")
    
    # Result line
    pdf.set_font('Helvetica', 'B', 8.5)
    pdf.set_text_color(37, 99, 235)
    pdf.write(4.5, "DEMO RESULT: ")
    pdf.set_font('Helvetica', 'B', 8.5)
    pdf.set_text_color(30, 41, 59)
    pdf.write(4.5, f"{result_str}\n")
    
    pdf.set_left_margin(20)
    pdf.set_x(20)
    pdf.set_y(y_start + 44 + 3)

def draw_poc_performance_table(pdf):
    pdf.ln(3)
    col_w1 = 18 # Row #
    col_w2 = 14 # Qty
    col_w3 = 45 # Customer Part
    col_w4 = 40 # Sourced Catalog
    col_w5 = 20 # Brand
    col_w6 = 15 # Conf %
    col_w7 = 18 # Logic
    table_x = 20
    
    # Header
    pdf.set_fill_color(30, 41, 59) # Slate Navy
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 7.5)
    
    y = pdf.get_y()
    pdf.rect(table_x, y, 170, 8, style="F")
    
    pdf.set_x(table_x + 2)
    pdf.cell(col_w1, 8, "BOM Row")
    pdf.cell(col_w2, 8, "Quantity")
    pdf.cell(col_w3, 8, "Customer Specification")
    pdf.cell(col_w4, 8, "Sourced Catalog MPN")
    pdf.cell(col_w5, 8, "Brand")
    pdf.cell(col_w6, 8, "Confidence")
    pdf.cell(col_w7, 8, "Logic", align="R")
    pdf.ln(8)
    
    rows = [
        {"row": "Row 1", "qty": "10", "spec": "WT2F-P170 | SICK Photoelectric", "mpn": "WT2F-P170", "brand": "SICK", "conf": "100%", "logic": "Exact Match"},
        {"row": "Row 2", "qty": "25", "spec": "Panasonic 100uF 16V Leaded Cap", "mpn": "16SEPF100M", "brand": "PANASONIC", "conf": "95%", "logic": "Semantic"},
        {"row": "Row 3", "qty": "5", "spec": "Mean Well 5V 10A PSU", "mpn": "RS-50-5", "brand": "MEAN WELL", "conf": "90%", "logic": "Heuristic"},
        {"row": "Row 4", "qty": "100", "spec": "0402 10k Resistor (TI/Vishay)", "mpn": "CRCW040210K0FKED", "brand": "VISHAY", "conf": "88%", "logic": "Fallback"}
    ]
    
    for i, r in enumerate(rows):
        if i % 2 == 0:
            pdf.set_fill_color(241, 245, 249)
        else:
            pdf.set_fill_color(255, 255, 255)
            
        y_before = pdf.get_y()
        row_h = 10
        pdf.rect(table_x, y_before, 170, row_h, style="F")
        
        pdf.set_y(y_before + 3)
        pdf.set_x(table_x + 2)
        
        # Print cells
        pdf.set_font('Helvetica', 'B', 8)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(col_w1, 4, clean_text(r["row"]))
        
        pdf.set_font('Helvetica', '', 8)
        pdf.set_text_color(51, 65, 85)
        pdf.cell(col_w2, 4, clean_text(r["qty"]))
        pdf.cell(col_w3, 4, clean_text(r["spec"]))
        pdf.cell(col_w4, 4, clean_text(r["mpn"]))
        pdf.cell(col_w5, 4, clean_text(r["brand"]))
        
        pdf.set_font('Helvetica', 'B', 8)
        pdf.set_text_color(37, 99, 235)
        pdf.cell(col_w6, 4, clean_text(r["conf"]))
        
        pdf.set_font('Helvetica', 'B', 7)
        pdf.set_text_color(51, 65, 85)
        pdf.cell(col_w7, 4, clean_text(r["logic"]), align="R")
        
        # Divider line
        pdf.set_draw_color(226, 232, 240)
        pdf.set_line_width(0.3)
        pdf.line(table_x, y_before + row_h, table_x + 170, y_before + row_h)
        
        pdf.set_y(y_before + row_h)
        
    pdf.ln(3)

def draw_integration_flowcharts(pdf):
    pdf.ln(3)
    y_start = pdf.get_y()
    
    # 2 equal columns of 82mm width each
    col_w = 82
    col_h = 66
    spacing = 6
    
    # Draw Column 1: Option A (Standalone Portal)
    pdf.set_fill_color(248, 250, 252)
    pdf.rect(20, y_start, col_w, col_h, style="F")
    pdf.set_draw_color(226, 232, 240)
    pdf.set_line_width(0.5)
    pdf.rect(20, y_start, col_w, col_h, style="D")
    
    pdf.set_y(y_start + 4)
    pdf.set_x(24)
    pdf.set_left_margin(24)
    
    pdf.set_font('Helvetica', 'B', 9)
    pdf.set_text_color(37, 99, 235)
    pdf.cell(col_w - 8, 5, clean_text("Option A: Standalone Portal"), new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(2.5)
    steps_a = [
        "1. Procurement team uploads Excel/PDF BOM",
        "2. Standalone FastAPI portal parses inputs",
        "3. Engineers audit fuzzy matches on interface",
        "4. Export finalized orders to standardized Excel",
        "5. Manual order placement in standard RS ERP"
    ]
    for step in steps_a:
        pdf.set_x(24)
        pdf.set_font('Helvetica', '', 8)
        pdf.set_text_color(51, 65, 85)
        pdf.multi_cell(col_w - 8, 4, clean_text(step), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        
    # Draw Column 2: Option B (Integrated API)
    pdf.set_fill_color(240, 246, 252) # Ice Blue
    pdf.rect(20 + col_w + spacing, y_start, col_w, col_h, style="F")
    pdf.set_draw_color(191, 219, 254)
    pdf.rect(20 + col_w + spacing, y_start, col_w, col_h, style="D")
    
    pdf.set_y(y_start + 4)
    pdf.set_x(20 + col_w + spacing + 4)
    pdf.set_left_margin(20 + col_w + spacing + 4)
    
    pdf.set_font('Helvetica', 'B', 9)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(col_w - 8, 5, clean_text("Option B: Fully Integrated API"), new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(2.5)
    steps_b = [
        "1. RFQ spreadsheet queue enters e-commerce site",
        "2. Automated webhook hits /search-bom endpoint",
        "3. Custom BERT transformer retrieves dense matches",
        "4. High-Speed exact bypass optimizes matching",
        "5. Output JSON populates CRM for auto-quotes"
    ]
    for step in steps_b:
        pdf.set_x(20 + col_w + spacing + 4)
        pdf.set_font('Helvetica', '', 8)
        pdf.set_text_color(51, 65, 85)
        pdf.multi_cell(col_w - 8, 4, clean_text(step), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        
    pdf.set_left_margin(20)
    pdf.set_x(20)
    pdf.set_y(y_start + col_h + 4)

def generate_pack_pdf(output_path):
    pdf = CustomerPackPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(20, 25, 20)
    pdf.alias_nb_pages()
    
    # ---------------- PAGE 1: COVER PAGE ----------------
    draw_cover_page(pdf)
    
    # ---------------- PAGE 2: INTRO & MEET AGENDA & IT Qs ----------------
    pdf.add_page()
    add_section_header(pdf, "Deliverable A: Discovery Call Agenda & Questionnaire")
    
    add_styled_paragraph(pdf, 
        "This pack contains the necessary customer-facing deliverables and professional templates to execute "
        "**Section 5 (Next Steps & Recommended Timeline)** of the RS Components AI Vector BOM Sourcing Proposal. "
        "It is designed to structure technical interactions and drive the live Proof of Concept (PoC) successfully."
    )
    
    add_subsection_header(pdf, "📅 Technical Discovery Call Agenda (45 Minutes)")
    
    add_bullet_point(pdf, "1", "**Introductions & Call Objectives** (5 Mins) - Aligning procurement leadership objectives.")
    add_bullet_point(pdf, "2", "**Architectural Overview** (15 Mins) - Explaining standard dockerized deployment, Qdrant hosting 630K+ component indexes, and offline custom fine-tuned **sourcebuddy-electronics-bert** transformer models ensuring complete GDPR compliance.")
    add_bullet_point(pdf, "3", "**Matching System Demonstration** (15 Mins) - Showcase exact match bypass, semantic fallback, and our four-layer parameter guard system.")
    add_bullet_point(pdf, "4", "**Scope Definition for Live PoC** (10 Mins) - Finalizing PoC deliverables and timeline.")
    
    pdf.ln(2)
    add_subsection_header(pdf, "📝 IT Leadership Discovery Questionnaire")
    
    add_question_box(pdf, "Q1", "Do you prefer on-premise hosting or virtual private cloud (VPC) deployment?", 
                     "SourceBuddy is packaged as standard Docker containers, fully compatible with standard AWS, Azure, and private servers.")
    add_question_box(pdf, "Q2", "Do you have GPU-enabled servers available, or should we provision CPU-only optimized models?", 
                     "Our custom electronics BERT embedding model is optimized for fast execution on standard dual-core CPUs.")
    
    # ---------------- PAGE 3: IT Qs CONT & PoC SCENARIOS ----------------
    pdf.add_page()
    
    add_question_box(pdf, "Q3", "What format is your master inventory/catalog data currently stored in?", 
                     "Supports automatic ETL ingestion pipelines from standard databases including MySQL, Oracle, SAP ERP, and CSV/TXT exports.")
    add_question_box(pdf, "Q4", "What is the current size of your active component/catalog list?", 
                     "Our matching engine is tested to handle 630K+ dense vector catalog records with sub-second retrieval latencies.")
    add_question_box(pdf, "Q5", "Do you have strict compliance rules preventing data from leaving the corporate firewall?", 
                     "SentenceTransformer operates entirely offline inside the Docker environment with HF_HUB_OFFLINE=1, keeping all data private.")
    
    add_section_header(pdf, "Deliverable B: Live Proof-of-Concept (PoC) Demonstration Guide")
    add_styled_paragraph(pdf, 
        "During the live demo, walk the customer through the following messy procurement scenarios "
        "which our engine resolves natively using domain-adapted heuristics:"
    )
    
    # Scenario Cards
    draw_poc_scenario_card(pdf, "1", "Delimited Split MPNs and Descriptions", 
                           "'WT2F-P170 | SICK Photoelectric Sensor' or unstructured raw logs.",
                           "Parser splits tokens, detects valid partWT2F-P170, and runs precise catalog searches.",
                           "100% Exact Match mapped in under 50 milliseconds.")
                           
    draw_poc_scenario_card(pdf, "2", "Alphanumeric Format Variations", 
                           "'TX Instr 10k res 0402' representing TI CRCW040210K0FKED.",
                           "Normalizes tokens, runs vector cosine similarity using sourcebuddy-electronics-bert.",
                           "High-Confidence Semantic Match (95%+) mapping equivalents.")
                           
    # ---------------- PAGE 4: PoC SCENARIO 3 & PoC TABLE & INTEGRATION ----------------
    pdf.add_page()
    
    draw_poc_scenario_card(pdf, "3", "Physical Parameter Protection", 
                           "'100uF 16V Capacitor' matching catalog component '100uF 50V'.",
                           "Parameter conflict guard extracts physical metrics via high-precision regex to penalize mismatched values.",
                           "Safety Mismatch Guarded (Score < 30%), prompting human review.")
    
    pdf.ln(2)
    add_subsection_header(pdf, "📊 PoC Sourcing Performance Summary Template")
    
    # Table
    draw_poc_performance_table(pdf)
    
    add_section_header(pdf, "Deliverable C: Workshop & Gap Analysis Framework")
    add_styled_paragraph(pdf, 
        "Our workshop helps map how the Sourcing API drops into your legacy ordering workflow. "
        "We support two architectural integration models:"
    )
    
    # Integration Flowcharts
    draw_integration_flowcharts(pdf)
    
    # ---------------- PAGE 5: ROI MODELING ----------------
    pdf.add_page()
    add_subsection_header(pdf, "📈 ROI & Value Realization Modeling")
    add_styled_paragraph(pdf, 
        "Justify the Perpetual License cost by mapping direct operational hours saved and quote turnaround speed boosts:"
    )
    
    # ROI Cards
    # Card 1: OPEX Labor Hours
    pdf.set_fill_color(248, 250, 252)
    pdf.rect(20, pdf.get_y(), 170, 32, style="F")
    pdf.set_draw_color(226, 232, 240)
    pdf.rect(20, pdf.get_y(), 170, 32, style="D")
    pdf.set_fill_color(37, 99, 235)
    pdf.rect(20, pdf.get_y(), 2, 32, style="F")
    
    pdf.set_y(pdf.get_y() + 3)
    pdf.set_x(25)
    pdf.set_left_margin(25)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 5, clean_text("1. Operational Expenditure (OPEX) Labor Savings"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_font('Helvetica', '', 9.5)
    pdf.set_text_color(51, 65, 85)
    pdf.multi_cell(0, 4.5, clean_text(
        "Current state manual sourcing takes ~2.5 hours per complex 300-line BOM spreadsheet.\n"
        "SourceBuddy automates 95% of lines instantly, leaving 10 minutes of manual verification.\n"
        "Value Realized: Saves approximately 2,300 labor hours per week based on 1,000 weekly BOMs."
    ), new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_left_margin(20)
    pdf.set_x(20)
    pdf.set_y(pdf.get_y() + 8)
    
    # Card 2: Quote Turnaround
    pdf.set_fill_color(240, 246, 252)
    pdf.rect(20, pdf.get_y(), 170, 32, style="F")
    pdf.set_draw_color(191, 219, 254)
    pdf.rect(20, pdf.get_y(), 170, 32, style="D")
    pdf.set_fill_color(37, 99, 235)
    pdf.rect(20, pdf.get_y(), 2, 32, style="F")
    
    pdf.set_y(pdf.get_y() + 3)
    pdf.set_x(25)
    pdf.set_left_margin(25)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 5, clean_text("2. Instant Turnaround & Quote-to-Cash Speed Boost"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_font('Helvetica', '', 9.5)
    pdf.set_text_color(51, 65, 85)
    pdf.multi_cell(0, 4.5, clean_text(
        "BOM quoting turnaround time drops from 48 hours to under 2 minutes globally.\n"
        "In distribution markets, returning the first accurate quote secures high quote-to-win conversions.\n"
        "Value Realized: Drives a conservative 5% increase in total won BOM orders."
    ), new_x="LMARGIN", new_y="NEXT")
    
    # Save the PDF
    pdf.output(output_path)
    print(f"Success! Classic interaction pack PDF successfully generated and saved to: {output_path}")

if __name__ == "__main__":
    import sys
    dest_path = "/home/rajasethupathy/AI_Vector_Bom_Sourcing/review_pack/customer_interaction_pack.pdf"
    if len(sys.argv) > 1:
        dest_path = sys.argv[1]
    
    generate_pack_pdf(dest_path)
