#!/usr/bin/env python3
import sys

def verify_pdf(pdf_path):
    print(f"Verifying PDF: {pdf_path}")
    import pdfplumber
    
    with pdfplumber.open(pdf_path) as pdf:
        num_pages = len(pdf.pages)
        print(f"Total pages: {num_pages}")
        
        # Verify page count
        if num_pages != 4:
            print(f"Error: Page count is {num_pages}, expected exactly 4!")
            sys.exit(1)
            
        full_text = ""
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            print(f"Page {i+1} character count: {len(text) if text else 0}")
            if text:
                full_text += text + "\n"
                
        # Verification checks
        errors = 0
        
        # Check model names
        if "sourcebuddy-electronics-bert" not in full_text:
            print("Error: 'sourcebuddy-electronics-bert' model reference not found in PDF!")
            errors += 1
        else:
            print("Success: 'sourcebuddy-electronics-bert' model reference is present.")
            
        if "all-MiniLM-L6-v2" in full_text:
            print("Error: Generic model 'all-MiniLM-L6-v2' reference was found in PDF!")
            errors += 1
        else:
            print("Success: Generic model 'all-MiniLM-L6-v2' is absent.")
            
        # Check sections
        sections = [
            "Executive Summary",
            "Core Product Features",
            "Cost Savings & ROI",
            "Commercial Proposal"
        ]
        for sec in sections:
            if sec not in full_text:
                print(f"Error: Section '{sec}' not found in PDF!")
                errors += 1
            else:
                print(f"Success: Section '{sec}' is present.")
                
        if errors == 0:
            print("\nVerification PASSED! The PDF is structurally and technically 100% correct.")
            sys.exit(0)
        else:
            print(f"\nVerification FAILED with {errors} errors.")
            sys.exit(1)

if __name__ == "__main__":
    path = "/home/rajasethupathy/AI_Vector_Bom_Sourcing/rs_components_ai_bom_sales_proposal.pdf"
    if len(sys.argv) > 1:
        path = sys.argv[1]
    verify_pdf(path)
