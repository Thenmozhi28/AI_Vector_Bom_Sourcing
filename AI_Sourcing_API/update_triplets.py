import json

triplets_file = "/app/model_cache/training_data/training_triplets.jsonl"

print("Reading existing triplets...")
with open(triplets_file, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
updated_count = 0

replacements = {
    "TX Instr 10k res 0402": {
        "positive_mpn": "CRCW040210K0FKED",
        "positive_description": "1819172 PSB_346947 Electronic Components, Power & Connectors PSSS_432964 Passive Components PSS_432996 Fixed Resistors PSF_433010 Surface Mount Resistors CRCW040210K0FKED Vishay Vishay CRCW 10 kΩ, 0402 Thick Film Surface Mount Resistor ±1 % 0.063 W - CRCW040210K0FKED"
    },
    "kemet 100nf 50v 0603 x7r": {
        "positive_mpn": "C0603C104K5RACTU",
        "positive_description": "8015347 PSB_346947 Electronic Components, Power & Connectors PSSS_432964 Passive Components PSS_432965 Capacitors PSF_432971 MLCCs - Multilayer Ceramic Capacitors C0603C104K5RACTU KEMET KEMET 100nF Multilayer Ceramic Capacitor MLCC, 50V dc V, ±10% , SMD"
    },
    "STM32 micro 64 pin": {
        "positive_mpn": "STM32F103RBT6",
        "positive_description": "0402251 PSB_346947 Electronic Components, Power & Connectors PSSS_430741 Semiconductors PSS_430753 Processors & Microcontrollers PSF_430995 Microcontrollers STM32F103RBT6 STMicroelectronics STMicroelectronics STM32F103RBT6, 32 bit ARM Cortex M3, STM32F103RB Microcontroller, 72 MHz, 128kB FLASH, 64-Pin LQFP"
    },
    "Molex 4 pin header": {
        "positive_mpn": "395311006",
        "positive_description": "0534429 PSB_346947 Electronic Components, Power & Connectors PSSS_435563 Connectors PSS_435683 PCB Connectors & Wire Housings PSF_435704 PCB Headers 395311006 Molex Molex PCB Header CN"
    },
    "1/4W 5% 10k thru hole": {
        "positive_mpn": "0141375",
        "positive_description": "0141375 PSB_346948 Electrical, Automation & Cables PSSS_421167 Automation & Control Gear PSS_421295 Process Control PSF_999999 Miscellaneous NEUTRAL RES CARBON .125W 10K"
    },
    "TE conn 2 position": {
        "positive_mpn": "282080-1",
        "positive_description": "1361723 PSB_346947 Electronic Components, Power & Connectors PSSS_435563 Connectors PSS_1602663 Automotive Electrical Connectors PSF_435716 Automotive Connector Accessories 282080-1 TE Connectivity TE Connectivity, AMP SUPERSEAL 1.5 Polyamide Female 2 Way Automotive Connector IT"
    }
}

# Keep original base datasets, but filter out the old un-updated custom triplets at the end
# The custom triplets are usually located at the end. We will load all and if they match the anchors,
# we will skip them during the first pass and then append our fresh updated ones.
seen_anchors = set(replacements.keys())

for line in lines:
    if not line.strip():
        continue
    try:
        data = json.loads(line)
        anchor = data.get("anchor")
        if anchor in seen_anchors:
            # Skip the old ones, we will append them cleanly
            continue
        new_lines.append(line)
    except Exception as e:
        new_lines.append(line)

# Now append our updated custom triplets, duplicated 50 times each for strong reinforcement
for anchor, payload in replacements.items():
    triplet = {
        "anchor": anchor,
        "positive_mpn": payload["positive_mpn"],
        "positive_description": payload["positive_description"],
        "negative_description": None
    }
    triplet_str = json.dumps(triplet) + "\n"
    # Duplicate 50 times!
    for _ in range(50):
        new_lines.append(triplet_str)
    print(f"Added 50 custom reinforced training triplets for: {anchor}")

with open(triplets_file, "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print(f"Finished saving triplets. Total lines in file: {len(new_lines)}")
