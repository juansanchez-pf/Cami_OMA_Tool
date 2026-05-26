# FinOps V3.2 Engine Logic

```mermaid
flowchart TD
    Start["🚀 Execute Global Pre-Audit"] --> MSA["⚖️ 1. MSA Governance"]
    Start --> Dates["⏱️ 2. Add-On Dates"]
    Start --> Fees["💰 3. Fee Structure"]
    Start --> Products["📦 4. Subscription Comp"]
    Start --> Billing["🧾 5. Billing & AP"]
    Start --> Comms["✉️ 6. Case Comms"]

    %% 1. MSA Logic
    MSA --> MSAType{"Opp Type?"}
    MSAType -->|New Business| MSANB{"MSA Type"}
    MSANB -->|Signed| MSANBWarning[/"Warning: Request/Send immediately"/]
    MSANB -->|Online| MSANBSuccess[/"Success: No additional docs"/]
    MSAType -->|Renewal| MSARen{"Compare vs Prior OF"}
    MSARen -->|Match| MSARenSuccess[/"Success: Continuity verified"/]
    MSARen -->|Mismatch| MSARenError[/"Error: Agreement change detected"/]

    %% 2. Date Logic
    Dates --> DateCheck{"Is Add-On?"}
    DateCheck -->|Yes| StartCheck{"OF Start < Contract Start?"}
    StartCheck -->|Yes| StartError[/"Error: Invalid Start"/]
    StartCheck -->|No| EndCheck{"OF End == Contract End?"}
    EndCheck -->|Yes| CotermSuccess[/"Success: Coterminus valid"/]
    EndCheck -->|No| CotermError[/"Error: Coterminus mismatch"/]

    %% 3. Fee Logic
    Fees --> SumCheck{"Schedule Sum == OF Total?"}
    SumCheck -->|No| SumError[/"Error: Total Fee Mismatch"/]
    SumCheck -->|Yes| OppFeeCheck{"Opp Type?"}
    OppFeeCheck -->|Add-On| Proration["Calculate Term Multiplier & Prorated Fee"]
    Proration --> ProrValidation{"Matches expected prorated?"}
    ProrValidation -->|No| ProrError[/"Error: Variance > 2%"/]
    ProrValidation -->|Yes| ProrSuccess[/"Success: Valid Proration"/]
    OppFeeCheck -->|Ren / NB| Uplift["Calculate YoY Growth"]
    Uplift --> UpliftResults[/"Flag as Uplift (📈), Reduction (📉), or Flat"/]

    %% 4. Product Logic
    Products --> QtyCheck["Compare SFDC Base vs OF Qty"]
    QtyCheck --> ProdStatus[/"Label: Upsell, Downsell, Dropped, Maintained, Brand New"/]
    Products --> TechCheck{"Tech Flags"}
    TechCheck --> Sandbox["Sandbox Added?"]
    TechCheck --> AI["AI/Analytics Added?"]
    Products --> LP["Learning Pass Entitlements"]
    LP --> LPTier{"Revenue Class"}
    LPTier -->|ENT| LPTier4[/"Expected Qty: 4"/]
    LPTier -->|CORP/MM| LPTier2[/"Expected Qty: 2"/]
    Products --> TierCheck["Tier Validation"]
    TierCheck --> TierResults[/"Flag: Core Support or Platform Tier changed"/]

    %% 5. Billing Logic
    Billing --> APContact{"AP Contact & Email?"}
    APContact -->|Yes| APSuccess[/"Success"/]
    APContact -->|No| APError[/"Error: Missing Info"/]
    Billing --> AddressCheck{"Address Blocks Present?"}
    AddressCheck -->|Yes| AddrSuccess[/"Success"/]
    AddressCheck -->|No| AddrError[/"Error: Missing Billing/Shipping"/]

    %% 6. Comms Logic
    Comms --> SigStatus{"Signature Status?"}
    SigStatus --> Await["Awaiting Signatory Info"]
    SigStatus --> Docu["DocuSign Ready"]
    Comms --> CommOpp{"Opp Type?"}
    CommOpp -->|Ren/Add-On| BaseComm["Generate Standard Instance Comm"]
    CommOpp -->|New Business| MissingHeader{"Header Fields Missing?"}
    MissingHeader -->|Yes| AppendMissing[/"Append Missing Fields to Email (Instance, Hosting, Admin)"/]
    Comms --> AlertAppend{"Appended Alerts"}
    AlertAppend -->|Sandbox Added| SBAlert[/"Append: Tear Down Warning"/]
    AlertAppend -->|AI Added| AIAlert[/"Append: Single Sandbox Enablement Warning"/]
