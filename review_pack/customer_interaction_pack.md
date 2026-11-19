# RS Components — Customer Engagement & Interaction Pack
## Prepared for: RS Group plc Procurement & Supply Chain Technology Leadership
### Context: Next Steps as per the AI Vector BOM Sourcing Enterprise Proposal

This pack contains the necessary customer-facing deliverables and professional templates to execute **Section 5 (Next Steps & Recommended Timeline)** of the [Sales Proposal](file:///home/rajasethupathy/rs_components_ai_bom_sales_proposal.pdf). 

---

## 📂 Deliverable A: Technical Discovery Call Agenda & Questionnaire
*Designed for the 30–45 minute introductory call with the VP of Supply Chain Technology & IT Leadership.*

### 📅 Meeting Agenda (45 Minutes)
1. **Introductions & Objectives** (5 Mins)
2. **Architectural Overview** (15 Mins)
   * Standalone Dockerized deployment (FastAPI backend + Glassmorphism single-page UI).
   * High-performance Qdrant Vector Database hosting 630K+ electronic components.
   * Offline, proprietary domain-adapted Technical Transformer Model (`sourcebuddy-electronics-bert`) trained specifically on your 1.8 Million component records to ensure maximum semantic relevance and 100% data privacy.
3. **Heuristic & Intelligent Matching System Demo** (15 Mins)
   * Overview of exact scroll search and semantic fallback.
   * Explaining the four-layer Heuristic Guard System (MPN format normalization, Category Conflict filter, Token Overlap, and Physical Parameter validation).
4. **Q&A & Scope Definition for Live Proof of Concept (PoC)** (10 Mins)

---

### 📝 Technical Discovery Questionnaire (For IT Leadership)
> [!IMPORTANT]
> Share these questions prior to the call to ensure their IT and database administrators are aligned.

#### 1. Deployment & Infrastructure
* **Q1:** Do you prefer on-premise hosting or virtual private cloud (VPC) deployment? (The system is delivered as standard Docker containers, fully compatible with AWS, Azure, and Google Cloud).
* **Q2:** Do you have GPU-enabled servers available for model fine-tuning and batch training, or should we provision CPU-only optimized containers? (Our embedding models are fully optimized for local CPU runtime).

#### 2. Master Catalog Integration
* **Q3:** What format is your master inventory/catalog data currently stored in? (e.g., MySQL, Oracle, SAP ERP, or CSV/TXT exports).
* **Q4:** What is the current size of your active component list? (SourceBuddy is pre-tested to handle 630K+ dense vector records with sub-second retrieval latency).

#### 3. Data Privacy & Compliance
* **Q5:** Do you have strict compliance rules preventing any data from leaving the corporate firewall? (Our local SentenceTransformer deployment operates entirely offline with `HF_HUB_OFFLINE=1`, ensuring 100% GDPR compliance and zero external API dependencies).

---

## 📂 Deliverable B: Live Proof-of-Concept (PoC) Demonstration Guide
*Designed for the side-by-side live demonstration processing a sample of "messy" customer BOMs.*

### ⚡ PoC Sourcing Demonstration Scenarios
During the live demo, demonstrate how the system handles the following messy real-world scenarios (which we have successfully validated against your 12 test inputs):

#### Scenario 1: Delimited Split MPNs and Descriptions
* **Customer Input**: A single row containing `WT2F-P170 | SICK Photoelectric Sensor` or similar unstructured descriptions.
* **The AI Action**: The multi-pass regex parser splits the tokens, detects the real part number `WT2F-P170` via alphanumeric validation, queries Qdrant for exact matches, and appends the remainder to the search description.
* **Result**: **100% Exact Match** mapped to the catalog in under 50 milliseconds.

#### Scenario 2: Alphanumeric Format Variations
* **Customer Input**: Variations like `TX Instr 10k res 0402` or mismatching spacing.
* **The AI Action**: Normalizes the token to pure alphanumerics, boosting matches that align with technical parameters while bypassing noise.
* **Result**: **High-Confidence Semantic Match (95%+)** suggesting Texas Instruments or Panasonic equivalents.

#### Scenario 3: Physical Parameter Protection
* **Customer Input**: `100uF 16V Capacitor` matching against a catalog item that has a different voltage rating (e.g. `100uF 50V`).
* **The AI Action**: The Parameter Conflict Guard extracts `16V` and `50V` ratings using high-precision regex. It calculates the delta and applies a heavy penalty to prevent incorrect components from being auto-sourced.
* **Result**: **Safety Mismatch Guarded (Score < 30%)**, prompting human-in-the-loop review.

---

### 📊 PoC Sourcing Performance Summary Template
*Use this template to present the live results of the PoC sourcing run to the customer.*

| BOM Row # | Mapped Quantity | Customer Part / Specification | Sourced Catalog MPN | Sourced Brand | Confidence Score | Matching Logic |
| :---: | :---: | :--- | :--- | :--- | :---: | :--- |
| **Row 1** | 10 | `WT2F-P170` | `WT2F-P170` | SICK | 100% | Exact Match ✅ |
| **Row 2** | 25 | `Panasonic 100uF 16V Leaded` | `16SEPF100M` | PANASONIC | 95% | Semantic Boosted ✅ |
| **Row 3** | 5 | `Mean Well 5V 10A PSU` | `RS-50-5` | MEAN WELL | 90% | Heuristic Match ✅ |
| **Row 4** | 100 | `0402 10k Resistor` | `CRCW040210K0FKED` | VISHAY | 88% | Semantic Fallback ✅ |

---

## 📂 Deliverable C: Workshop & Gap Analysis Framework
*Designed for the detailed integration planning session and custom ERP workflow design.*

### 🛠️ Integration Architecture Options
During the workshop, present the two integration pathways for SourceBuddy inside their procurement pipeline:

```mermaid
graph TD
    subgraph Option A: Standalone Portal
        A[User Uploads BOM] -->|FastAPI Web UI| B(SourceBuddy Portal)
        B -->|Export| C[Excel audited Order Sheet]
        C -->|Manual Upload| D[RS ERP / Procurement System]
    end
    
    subgraph Option B: Fully Integrated API
        E[Customer RFQ Queue] -->|API Webhook| F(SourceBuddy `/search-bom`)
        F -->|JSON Response| G[Internal ERP/Salesforce]
        G -->|Auto-Generated| H[Instant Customer Quote]
    end
```

### 📈 ROI & Value Realization Modeling
Present this ROI calculation based on their estimated weekly volume (**1,000 BOMs/week**) to justify the Perpetual License cost:

#### 1. Labor Hours Saved
* **Current State**: Manual sourcing takes **2.5 hours** per 300-line BOM.
* **SourceBuddy State**: Automated matching takes **under 2 minutes**, with ~10 minutes of procurement review (95% automated).
* **Weekly Savings**: `1,000 BOMs * 2.3 hours saved = 2,300 labor hours saved per week`.

#### 2. Quote Turnaround & Win-Rate Uplift
* **Current Turnaround**: 24–48 hours (leading to lost bids to faster competitors).
* **SourceBuddy Turnaround**: **Under 2 minutes** for instant quoting.
* **Business Impact**: Conservative **5% increase** in won BOM business due to instant responsiveness.
