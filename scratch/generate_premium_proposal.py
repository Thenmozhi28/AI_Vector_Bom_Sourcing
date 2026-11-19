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
    # Fallback to ignore any remaining unmappable unicode characters safely
    return text.encode('latin-1', 'replace').decode('latin-1')

class PremiumProposalPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            # Thin top rule
            self.set_draw_color(226, 232, 240) # light slate gray
            self.set_line_width(0.4)
            self.line(self.l_margin, 18, self.w - self.r_margin, 18)
            
            # Header text
            self.set_y(10)
            self.set_font('Helvetica', 'B', 7.5)
            self.set_text_color(148, 163, 184) # slate gray
            self.cell(0, 8, clean_text("RS GROUP PLC  |  ENTERPRISE AI SOLUTIONS PROPOSAL"), align="L", new_x="LMARGIN", new_y="NEXT")
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
            self.cell(0, 10, clean_text("CONFIDENTIAL  -  FOR RS GROUP / RS COMPONENTS ONLY"), align="L", new_x="LMARGIN", new_y="LAST")
            # Right aligned: Page numbers
            self.cell(0, 10, f"Page {self.page_no()} of {self.pages_count}", align="R")

def draw_cover_page(pdf):
    pdf.add_page()
    # Deep Navy Header Band (covers top half)
    pdf.set_fill_color(24, 43, 73) # Deep Slate Navy (#182B49)
    pdf.rect(0, 0, 210, 125, style="F")
    
    # Tech Teal Horizontal Divider Strip
    pdf.set_fill_color(0, 150, 166) # Tech Teal (#0096A6)
    pdf.rect(0, 125, 210, 6, style="F")
    
    # Title Text in Navy Band
    pdf.set_y(40)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(0, 150, 166) # Tech Teal accent color
    pdf.cell(0, 6, clean_text("ENTERPRISE CAPABILITY & PRICE PROPOSAL"), align="L", new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(3)
    pdf.set_font('Helvetica', 'B', 28)
    pdf.set_text_color(255, 255, 255) # White
    pdf.multi_cell(0, 12, clean_text("AI VECTOR BOM\nSOURCING PLATFORM"), align="L", new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(6)
    pdf.set_font('Helvetica', 'I', 12.5)
    pdf.set_text_color(226, 232, 240) # light slate blue/white
    pdf.cell(0, 8, clean_text("Proprietary Technical Transformer & Vector Matching API"), align="L", new_x="LMARGIN", new_y="NEXT")
    
    # Metadata in White Bottom Half
    pdf.set_y(155)
    
    # Draw left border accent in metadata area
    pdf.set_fill_color(24, 43, 73) # Navy bar
    pdf.rect(20, 155, 2, 70, style="F")
    
    # Prepared For section
    pdf.set_x(26)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(100, 116, 139) # Secondary gray
    pdf.cell(0, 5, clean_text("PREPARED FOR"), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(26)
    pdf.set_font('Helvetica', 'B', 13)
    pdf.set_text_color(24, 43, 73) # Main Navy
    pdf.cell(0, 6, clean_text("RS Components / RS Group plc"), align="L", new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(5)
    # Prepared By section
    pdf.set_x(26)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(0, 5, clean_text("PREPARED BY"), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(26)
    pdf.set_font('Helvetica', 'B', 13)
    pdf.set_text_color(24, 43, 73)
    pdf.cell(0, 6, clean_text("Senior Enterprise Solutions Director"), align="L", new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(5)
    # Date & Version
    pdf.set_x(26)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(0, 5, clean_text("DATE & AUDIT VERSION"), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(26)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(24, 43, 73)
    pdf.cell(0, 6, clean_text("May 2026  |  v1.2 (100% Codebase Validated)"), align="L", new_x="LMARGIN", new_y="NEXT")

def add_section_header(pdf, title):
    pdf.ln(5)
    pdf.set_font('Helvetica', 'B', 14)
    pdf.set_text_color(24, 43, 73) # Navy (#182B49)
    pdf.cell(0, 8, clean_text(title), new_x="LMARGIN", new_y="NEXT")
    
    # Styled underline
    pdf.set_draw_color(0, 150, 166) # Tech Teal (#0096A6)
    pdf.set_line_width(1.0)
    pdf.line(pdf.l_margin, pdf.get_y() - 1, pdf.w - pdf.r_margin, pdf.get_y() - 1)
    pdf.ln(4)

def add_subsection_header(pdf, title):
    pdf.ln(3)
    # Vertical accent bar
    x = pdf.get_x()
    y = pdf.get_y()
    pdf.set_fill_color(0, 150, 166) # Tech Teal
    pdf.rect(x, y + 1.2, 2.5, 7.5, style="F")
    
    # Title Text
    pdf.set_x(x + 5)
    pdf.set_font('Helvetica', 'B', 10.5)
    pdf.set_text_color(24, 43, 73) # Navy
    pdf.cell(0, 9, clean_text(title), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

def add_styled_paragraph(pdf, text):
    text = clean_text(text)
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(51, 65, 85) # Slate Dark Gray (#334155)
    
    # Split text by bold markers
    parts = text.split("**")
    is_bold = False
    
    # Set custom line height
    for part in parts:
        if is_bold:
            pdf.set_font('Helvetica', 'B', 10)
        else:
            pdf.set_font('Helvetica', '', 10)
        pdf.write(5.8, part)
        is_bold = not is_bold
    pdf.ln(7.5) # paragraph bottom margin

def add_callout_box(pdf, text, title="THE SOLUTION:"):
    text = clean_text(text)
    title = clean_text(title)
    pdf.ln(2)
    y_start = pdf.get_y()
    
    # Estimate line height and count to draw perfect rectangle
    char_limit = 82
    lines = 0
    for paragraph in text.split('\n'):
        lines += max(1, math.ceil(len(paragraph) / char_limit))
    
    height = lines * 5.8 + 14 # padding + title space
    
    # Draw background panel
    pdf.set_fill_color(240, 246, 252) # Ice Blue (#F0F6FC)
    pdf.rect(20, y_start, 170, height, style="F")
    
    # Draw thick left border accent
    pdf.set_fill_color(0, 150, 166) # Tech Teal
    pdf.rect(20, y_start, 2.5, height, style="F")
    
    # Write Callout Title
    pdf.set_y(y_start + 4)
    pdf.set_x(26)
    pdf.set_left_margin(26)
    pdf.set_right_margin(24)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(0, 150, 166) # Tech Teal
    pdf.cell(0, 5, title, new_x="LMARGIN", new_y="NEXT")
    
    # Write Callout Content
    pdf.set_font('Helvetica', 'I', 10)
    pdf.set_text_color(24, 43, 73) # Navy text
    pdf.multi_cell(0, 5.5, text, new_x="LMARGIN", new_y="NEXT")
    
    # Reset page margins and position
    pdf.set_left_margin(20)
    pdf.set_right_margin(20)
    pdf.set_x(20)
    pdf.set_y(y_start + height + 4)

def draw_metrics_grid(pdf):
    pdf.ln(3)
    y_start = pdf.get_y()
    
    card_w = 82
    card_h = 25
    spacing = 6
    
    metrics = [
        {"value": "95%+", "label": "SEMANTIC MATCH RATE", "desc": "Instantly resolves typos & fuzzy inputs"},
        {"value": "1.8M Components", "label": "TRAINED ELECTRONICS MODEL", "desc": "Custom sourcebuddy-electronics-bert"},
        {"value": "< 5 Seconds", "label": "API RESPONSE SPEED", "desc": "High-speed exact match bypass"},
        {"value": "100% Secure", "label": "AIR-GAPPED COMPLIANCE", "desc": "Private Docker/AWS deployment"}
    ]
    
    for i, m in enumerate(metrics):
        row = i // 2
        col = i % 2
        
        x = 20 + col * (card_w + spacing)
        y = y_start + row * (card_h + spacing)
        
        # Draw card background
        pdf.set_fill_color(248, 250, 252) # Light neutral slate gray
        pdf.rect(x, y, card_w, card_h, style="F")
        
        # Draw metric left border stripe
        pdf.set_fill_color(33, 150, 243) # Royal Blue accent
        pdf.rect(x, y, 1.5, card_h, style="F")
        
        # Metric Value
        pdf.set_y(y + 2.5)
        pdf.set_x(x + 5)
        pdf.set_font('Helvetica', 'B', 12.5)
        pdf.set_text_color(33, 150, 243) # Royal Blue
        pdf.cell(0, 5, clean_text(m["value"]), new_x="LMARGIN", new_y="NEXT")
        
        # Metric Label
        pdf.set_y(y + 8)
        pdf.set_x(x + 5)
        pdf.set_font('Helvetica', 'B', 7.5)
        pdf.set_text_color(24, 43, 73) # Navy
        pdf.cell(0, 4, clean_text(m["label"]), new_x="LMARGIN", new_y="NEXT")
        
        # Metric Description
        pdf.set_y(y + 12.5)
        pdf.set_x(x + 5)
        pdf.set_font('Helvetica', '', 8)
        pdf.set_text_color(100, 116, 139) # Secondary slate gray
        pdf.cell(0, 4, clean_text(m["desc"]), new_x="LMARGIN", new_y="NEXT")
        
    pdf.set_y(y_start + 2 * card_h + spacing + 4)

def add_bullet_point(pdf, title, desc):
    title = clean_text(title)
    desc = clean_text(desc)
    pdf.ln(1)
    y = pdf.get_y()
    
    # Bullet dot (small teal square)
    pdf.set_fill_color(0, 150, 166)
    pdf.rect(22, y + 1.8, 2, 2, style="F")
    
    # Text Indent
    pdf.set_left_margin(27)
    pdf.set_x(27)
    
    # Title
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(24, 43, 73)
    pdf.cell(0, 5, title, new_x="LMARGIN", new_y="NEXT")
    
    # Description (handles wrapped line bold split)
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(51, 65, 85)
    
    parts = desc.split("**")
    is_bold = False
    for part in parts:
        if is_bold:
            pdf.set_font('Helvetica', 'B', 10)
        else:
            pdf.set_font('Helvetica', '', 10)
        pdf.write(5.5, part)
        is_bold = not is_bold
    pdf.ln(7)
    
    # Restore margins
    pdf.set_left_margin(20)
    pdf.set_x(20)

def draw_commercial_table(pdf):
    pdf.ln(3)
    col_w1 = 45 # Category
    col_w2 = 85 # Description
    col_w3 = 40 # Cost
    table_x = 20
    
    # Header Row
    pdf.set_fill_color(24, 43, 73) # Deep Navy
    pdf.set_text_color(255, 255, 255) # White
    pdf.set_font('Helvetica', 'B', 9.5)
    
    y = pdf.get_y()
    pdf.rect(table_x, y, 170, 9, style="F")
    
    pdf.set_x(table_x + 3)
    pdf.cell(col_w1, 9, clean_text("Investment Category"), align="L")
    pdf.cell(col_w2, 9, clean_text("Description"), align="L")
    pdf.cell(col_w3 - 6, 9, clean_text("Estimated Cost"), align="R")
    pdf.ln(9)
    
    rows = [
        {
            "category": "Enterprise Perpetual\nLicense (One-Time)",
            "desc": "Uncapped API usage, unlimited users. Full deployment rights within RS Group infrastructure, hosted securely locally.",
            "cost": "$250,000 - $350,000"
        },
        {
            "category": "Custom Integration\n& Training (One-Time)",
            "desc": "Professional services to train the AI model on RS catalog numbers, ERP mappings, and setup system parameters.",
            "cost": "$45,000"
        },
        {
            "category": "Premium AMC\n(Annual)",
            "desc": "20% of Base License. Includes 24/7 SLA Support, quarterly retrainings of the BERT technical model, and updates.",
            "cost": "$60,000 / year"
        }
    ]
    
    # Render Data Rows
    for i, r in enumerate(rows):
        # Alternating background colors
        if i % 2 == 0:
            pdf.set_fill_color(248, 250, 252) # Light Slate Gray
        else:
            pdf.set_fill_color(255, 255, 255) # Pure White
            
        y_before = pdf.get_y()
        row_h = 17 # fixed row height designed to perfectly accommodate multiline cells
        
        pdf.rect(table_x, y_before, 170, row_h, style="F")
        
        # Column 1: Category (Bold Navy)
        pdf.set_y(y_before + 3)
        pdf.set_x(table_x + 3)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.set_text_color(24, 43, 73)
        pdf.multi_cell(col_w1 - 4, 4.5, clean_text(r["category"]))
        
        # Column 2: Description
        pdf.set_y(y_before + 3)
        pdf.set_x(table_x + col_w1 + 3)
        pdf.set_font('Helvetica', '', 8.5)
        pdf.set_text_color(51, 65, 85)
        pdf.multi_cell(col_w2 - 6, 4.5, clean_text(r["desc"]))
        
        # Column 3: Cost (Bold Royal Blue)
        pdf.set_y(y_before + 5)
        pdf.set_x(table_x + col_w1 + col_w2)
        pdf.set_font('Helvetica', 'B', 9.5)
        pdf.set_text_color(33, 150, 243) # Royal Blue
        pdf.cell(col_w3 - 3, 5, clean_text(r["cost"]), align="R")
        
        # Draw thin divider line at the bottom
        pdf.set_draw_color(226, 232, 240)
        pdf.set_line_width(0.5)
        pdf.line(table_x, y_before + row_h, table_x + 170, y_before + row_h)
        
        # Reset position to start of next row
        pdf.set_y(y_before + row_h)
        
    pdf.ln(3)

def draw_architecture_diagram(pdf):
    pdf.ln(5)
    y_start = pdf.get_y()
    
    # Background panel for diagram
    pdf.set_fill_color(248, 250, 252) # Light slate neutral
    pdf.rect(20, y_start, 170, 48, style="F")
    
    # Border
    pdf.set_draw_color(226, 232, 240)
    pdf.set_line_width(0.5)
    pdf.rect(20, y_start, 170, 48, style="D")
    
    # Left colored accent tag
    pdf.set_fill_color(0, 150, 166) # Tech Teal
    pdf.rect(20, y_start, 2, 48, style="F")
    
    # Title
    pdf.set_y(y_start + 4)
    pdf.set_x(26)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(24, 43, 73)
    pdf.cell(0, 5, "ENTERPRISE ARCHITECTURE FLOW", new_x="LMARGIN", new_y="NEXT")
    
    y = y_start + 18
    
    # Box 1: E-Commerce Frontend / ERP
    pdf.set_fill_color(255, 255, 255)
    pdf.set_draw_color(100, 116, 139)
    pdf.set_line_width(0.3)
    pdf.rect(26, y, 38, 15, style="DF")
    pdf.set_y(y + 5)
    pdf.set_x(26)
    pdf.set_font('Helvetica', 'B', 8)
    pdf.set_text_color(24, 43, 73)
    pdf.cell(38, 5, "ERP / Frontend BOM", align="C")
    
    # Arrow 1
    pdf.set_fill_color(148, 163, 184)
    pdf.rect(65, y + 7, 8, 1, style="F")
    
    # Box 2: FastAPI Gateway
    pdf.set_fill_color(240, 246, 252)
    pdf.set_draw_color(33, 150, 243)
    pdf.rect(74, y - 5, 42, 25, style="DF")
    pdf.set_y(y)
    pdf.set_x(74)
    pdf.set_font('Helvetica', 'B', 8)
    pdf.cell(42, 5, "FastAPI Gateway", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font('Helvetica', '', 7)
    pdf.set_x(74)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(42, 4, "(High-Speed Routing)", align="C")
    
    # Arrow 2
    pdf.set_fill_color(148, 163, 184)
    pdf.rect(117, y + 7, 8, 1, style="F")
    
    # Box 3: Transformer Model
    pdf.set_fill_color(24, 43, 73) # Navy
    pdf.set_draw_color(24, 43, 73)
    pdf.rect(126, y - 10, 58, 15, style="DF")
    pdf.set_y(y - 8)
    pdf.set_x(126)
    pdf.set_font('Helvetica', 'B', 7.5)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(58, 4, "sourcebuddy-electronics-bert", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font('Helvetica', '', 6.5)
    pdf.set_x(126)
    pdf.cell(58, 4, "Custom Embedding Model", align="C")
    
    # Arrow 3 (Down)
    pdf.rect(154, y + 5, 1, 6, style="F")
    
    # Box 4: Qdrant Database
    pdf.set_fill_color(0, 150, 166) # Teal
    pdf.set_draw_color(0, 150, 166)
    pdf.rect(126, y + 11, 58, 15, style="DF")
    pdf.set_y(y + 13)
    pdf.set_x(126)
    pdf.set_font('Helvetica', 'B', 8)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(58, 4, "Qdrant Vector Database", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font('Helvetica', '', 7)
    pdf.set_x(126)
    pdf.cell(58, 4, "630K+ Component Indexes", align="C")
    
    pdf.set_y(y_start + 48 + 4)

def generate_pdf(output_path):
    pdf = PremiumProposalPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(20, 25, 20)
    pdf.alias_nb_pages()
    
    # ---------------- PAGE 1: COVER PAGE ----------------
    draw_cover_page(pdf)
    
    # ---------------- PAGE 2: EXEC SUMMARY & INTRO ----------------
    pdf.add_page()
    add_section_header(pdf, "1. Executive Summary")
    
    add_styled_paragraph(pdf, 
        "RS Components / RS Group plc processes thousands of complex **Request for Quotes (RFQs)** globally every single week. "
        "Your customers - high-value engineering firms and contract manufacturers - rely on you to rapidly source hundreds of unique "
        "electronics components from a single Bill of Materials (BOM)."
    )
    
    add_styled_paragraph(pdf, 
        "However, the industry standard for BOM ingestion is highly inefficient. Customers submit messy spreadsheets containing "
        "typos, partial manufacturer part numbers (MPNs), and inconsistent formatting. Today, resolving these discrepancies "
        "requires hours of manual intervention by your application engineers and sales staff, leading to long quoting delays."
    )
    
    add_callout_box(pdf, 
        "We have developed a proprietary AI Vector BOM Sourcing API. By leveraging advanced machine learning "
        "and contextual vector embeddings, our system understands the semantic intent of a part number or description, "
        "moving far beyond the limitations of legacy 'exact string match' databases. This allows RS Components to "
        "instantly quote messy BOMs, suggest in-stock alternatives, and dramatically accelerate the quote-to-cash cycle.",
        title="THE SOLUTION"
    )
    
    add_section_header(pdf, "2. Core Product Features")
    add_styled_paragraph(pdf, 
        "Our API is engineered specifically for global supply chain procurement and drops directly into your "
        "existing ERP and frontend web architecture. Here are the core technical capabilities:"
    )
    
    # Draw Metrics Grid to summarize core feature strengths
    draw_metrics_grid(pdf)
    
    # ---------------- PAGE 3: TECH DETAILS ----------------
    pdf.add_page()
    
    # Insert Architecture Diagram
    draw_architecture_diagram(pdf)
    
    add_subsection_header(pdf, "A. Semantic Vector Matching Engine")
    add_styled_paragraph(pdf, 
        "Legacy SQL databases require exact matches. If a customer types 'TX Instr 10k res 0402' instead of 'Texas Instruments CRCW040210K0FKED', "
        "standard systems fail to find the product. Our API leverages dense vector representations generated by the custom fine-tuned "
        "**sourcebuddy-electronics-bert** transformer to recognize that these strings represent the exact same component, achieving **95%+ auto-match rates** on unstructured BOM data."
    )
    
    add_subsection_header(pdf, "B. Intelligent Cross-Referencing & Substitution")
    add_styled_paragraph(pdf, 
        "Supply chain volatility means critical parts are often out of stock. When a requested part is unavailable, our AI analyzes "
        "technical specifications (voltage, tolerance, footprint) and instantly suggests the closest functional equivalent "
        "that RS Components currently holds in stock, ensuring you **never lose a line-item sale** due to stockouts."
    )
    
    add_subsection_header(pdf, "C. Custom Fine-Tuned Enterprise Models")
    add_styled_paragraph(pdf, 
        "This is not a generic off-the-shelf OpenAI model. We custom-train the AI exclusively on RS Components' historical "
        "sales logs, proprietary catalog definitions, and internal part numbers using our SentenceTransformer training architecture "
        "(`train_custom_transformer.py`). The model **sourcebuddy-electronics-bert** is specialized to understand your specific inventory taxonomy."
    )
    
    add_subsection_header(pdf, "D. High-Throughput REST API & High-Speed Bypass")
    add_styled_paragraph(pdf, 
        "Built on a scalable, production-ready Celery/Redis architecture, our REST API exposes high-speed endpoints. It can ingest "
        "a 5,000-line CSV/Excel BOM from your e-commerce frontend and return fully matched and priced results in **under 5 seconds**. "
        "By utilizing a **High-Speed Bypass Optimization**, exact database matches bypass vector model inference entirely, maximizing throughput."
    )
    
    add_subsection_header(pdf, "E. Private Cloud / Air-Gapped Deployment")
    add_styled_paragraph(pdf, 
        "Delivered via secure Docker containers, the entire pipeline runs locally or in your private cloud. There are no external API calls "
        "and **no third-party cloud data leaks**, ensuring strict GDPR and corporate data privacy compliance."
    )
    
    # ---------------- PAGE 4: BUSINESS CASE & COMMERCE ----------------
    pdf.add_page()
    add_section_header(pdf, "3. The Business Case: Cost Savings & ROI")
    
    add_styled_paragraph(pdf, 
        "Investing in this API is not an IT expense; it is a direct driver of corporate revenue and operational cost reduction. "
        "Here is the quantified impact on your bottom line:"
    )
    
    add_bullet_point(pdf, 
        "1. Massive Reduction in Operational Overhead (OPEX)",
        "An application engineer currently spends 2-3 hours manually scrubbing and cross-referencing a complex 300-line BOM. "
        "The API automates 95% of lines instantly. For 1,000 BOMs processed globally per week, this saves approximately "
        "**2,500 labor hours per week**, allowing engineering staff to focus on high-margin customer solutions rather than data entry."
    )
    
    add_bullet_point(pdf, 
        "2. Increased Quote-to-Win Ratio (Revenue Generation)",
        "In the component distribution market, the first vendor to return an accurate, priced quote wins the business. "
        "By reducing turnaround times from **48 hours to 2 minutes**, RS Components captures spot-buys and contract manufacturing orders."
    )
    
    add_bullet_point(pdf, 
        "3. Optimized Inventory Turnover",
        "By automatically recommending available cross-references for out-of-stock items, you actively clear warehouse stock rather than losing the line-item to competitors like Mouser or DigiKey."
    )
    
    add_section_header(pdf, "4. Commercial Proposal (One-Time + AMC Model)")
    add_styled_paragraph(pdf, 
        "Unlike standard SaaS competitors that charge expensive recurring licensing fees, we offer an **Enterprise Perpetual License** model. "
        "This secures a drastically lower Total Cost of Ownership (TCO) over a 3-to-5 year timeline."
    )
    
    # Commercial Table
    draw_commercial_table(pdf)
    
    # Payback and next steps
    add_styled_paragraph(pdf, 
        "**ROI Timeline:** Assuming standard engineering labor rates and a conservative 5% increase in won BOMs, "
        "the **payback period for this capital expenditure is estimated at 4 to 6 months**."
    )
    
    # Save the PDF
    pdf.output(output_path)
    print(f"Success! Premium sales proposal PDF successfully generated and saved to: {output_path}")

if __name__ == "__main__":
    import sys
    dest_path = "/home/rajasethupathy/AI_Vector_Bom_Sourcing/rs_components_ai_bom_sales_proposal.pdf"
    if len(sys.argv) > 1:
        dest_path = sys.argv[1]
    
    generate_pdf(dest_path)
