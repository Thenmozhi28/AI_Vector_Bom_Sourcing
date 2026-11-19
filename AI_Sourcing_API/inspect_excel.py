import pandas as pd
import sys
import os

excel_path = r"uploads/Sample10_RS.xlsx"
if not os.path.exists(excel_path):
    print(f"File not found: {excel_path}")
    # Let's list uploads folder
    if os.path.exists("AI_Sourcing_API/uploads"):
        print("Uploads folder contents:", os.listdir("AI_Sourcing_API/uploads"))
    sys.exit(1)

try:
    df = pd.read_excel(excel_path)
    print("Columns:", list(df.columns))
    print("Total rows:", len(df))
    print("First 10 rows:")
    print(df.head(10))
    
    # Search for MPS-6033XP
    print("\nSearching for MPS-6033XP in dataframe:")
    for col in df.columns:
        matches = df[df[col].astype(str).str.contains("MPS-6033XP|6033XP|MPS6033XP", case=False, na=False)]
        if not matches.empty:
            print(f"Found in column '{col}':")
            print(matches)
except Exception as e:
    print(f"Error reading Excel: {e}")
