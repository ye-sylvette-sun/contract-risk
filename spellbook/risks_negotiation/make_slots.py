import json
import re
from pathlib import Path


def natural(name):
    """Sort key matching how VS Code's explorer orders these folders.

    The explorer compares with numeric collation, so a leading `20` sorts
    before `97` and `109` rather than between `131` and `226` as plain ASCII
    would have it. Digit runs compare as numbers, everything else casefolded.
    """
    return [int(p) if p.isdigit() else p.lower()
            for p in re.split(r"(\d+)", name)]

# Each contract's two sides: the ROLE the document gives the party, plus enough
# of its actual name to be recognisable without opening parties.json. A bare
# role (`company`, `recipient`) does not say who it is once the folder is open.
PARTIES = {
 "109FedCl1_agreement_for_purchase_and":        [("seller_1200_sixth_street",   "1200 6th Street LLC"),
                                                 ("buyer_united_states",        "(blank in the form; United States / GSA)")],
 "109FedCl1_real_estate_option_agreement":      [("optionor_1200_sixth_street", "1200 6th Street LLC"),
                                                 ("optionee_united_states",     "United States, acting through GSA")],
 "118FSupp3d802_membership_agreement":          [("angies_list",                "Angie's List, Inc."),
                                                 ("member_user",                "You, the user (Moore)")],
 "122FSupp3d1157_broker_dealer_agreement":      [("broker_dealer_goldman_sachs", "Goldman, Sachs & Co. (BD)"),
                                                 ("company_presbyterian",       "Presbyterian Healthcare Services")],
 "129FSupp3d782_the_policy":                    [("insurer_illinois_union",     "Illinois Union Insurance Company"),
                                                 ("insured_rembrandt",          "Rembrandt Enterprises, Inc.")],
 "131FSupp3d635_restricted_cash_agreement_to":  [("company_shell",              "Shell Exploration & Production Company"),
                                                 ("employee_ryder",             "Ryder, the employee")],
 "20FSupp3d709_nols_student_agreement_including":[("school_nols",               "National Outdoor Leadership School"),
                                                 ("student_plotkin",            "the student / parent (Plotkin)")],
 "226FSupp3d719_pond_agreement":                [("cab_pls_loan_store",         "PLS Loan Store of Texas, Inc. (CAB)"),
                                                 ("customer_pond",              "Pond, the Customer")],
 "226FSupp3d719_vine_agreement":                [("cab_pls_loan_store",         "PLS Loan Store of Texas, Inc. (CAB)"),
                                                 ("customer_vine",              "Vine, the Customer")],
 "266FSupp3d666_employment_agreement":          [("corporation_luxoft",         "Luxoft USA, Inc."),
                                                 ("executive_lankau",           "Maik Lankau")],
 "390FSupp3d645_the_agreement":                 [("company_delta_speir",        "Delta Speir Plantation, LLC"),
                                                 ("consultants_gee_martin",     "Raymond M. Gee and Adam A. Martin")],
 "527BR351_liquidating_trust_agreement":        [("trustee_uecker",             "Susan L. Uecker, as trustee"),
                                                 ("debtor_mortgage_fund_08",    "Mortgage Fund '08 LLC")],
 "562FSupp2d260_settlement_agreement_the_agreement":
                                                [("argus_research_group",       "Argus Research Group, Inc."),
                                                 ("petroleum_argus",            "Petroleum Argus Limited")],
 "721FSupp2d613_license_agreement_with_the":    [("city_of_youngstown",         "City of Youngstown, Ohio"),
                                                 ("hardrives_paving",           "Hardrives Paving & Construction, Inc.")],
 "808FSupp2d552_trademark_license_and_cooperation":
                                                [("licensor_rank_licensing",    "Rank Licensing, Inc."),
                                                 ("licensee_peter_morton",      "Peter A. Morton")],
 "817FSupp2d1357_settlement_and_release_agreement":
                                                [("releasors_entaire_rosen",    "Entaire Global / Rosen / Muirhead"),
                                                 ("releasee_american_guarantee", "American Guarantee & Liability Insurance Co.")],
 "871FSupp2d671_2005_noncompetition_agreement": [("company_ajuba",              "Ajuba International, Inc."),
                                                 ("shareholder_saharia",        "Devendra Saharia")],
 "97FSupp3d548_mortgage_loan_purchase_agreement":
                                                [("sellers_emc_morgan_stanley", "EMC Mortgage Corp. / Morgan Stanley MCH"),
                                                 ("purchaser_bear_stearns",     "Bear Stearns Asset Backed Securities I LLC")],
 "982FSupp2d518_2003_mta":                      [("provider_st_jude",           "St. Jude Children's Research Hospital"),
                                                 ("recipient_june_upenn",       "Dr. Carl June / Univ. of Pennsylvania")],
 "982FSupp2d518_2007_mta":                      [("provider_st_jude",           "St. Jude Children's Research Hospital"),
                                                 ("recipient_june_upenn",       "Dr. Carl June / Univ. of Pennsylvania")],
}

ROOT = Path(".")
prompts = sorted(p.stem for p in (ROOT / "spellbook" / "chatbot" / "prompt").glob("*.txt"))
missing = set(prompts) - set(PARTIES)
extra = set(PARTIES) - set(prompts)
assert not missing and not extra, f"missing {missing}, extra {extra}"

out = ROOT / "spellbook" / "risks_negotiation" / "output"
out.mkdir(parents=True, exist_ok=True)
made = kept = 0
for cid, sides in PARTIES.items():
    # One directory per contract, one file per party. Flat
    # `<contract>_<party>.txt` names ran past 70 characters.
    (out / cid).mkdir(parents=True, exist_ok=True)
    for slug, who in sides:
        p = out / cid / f"{slug}.txt"
        if p.exists():
            kept += 1
        else:
            p.write_text("", encoding="utf-8")
            made += 1
        print(f"  {cid}/{slug}.txt".ljust(74) + who)
(ROOT / "spellbook" / "risks_negotiation" / "parties.json").write_text(
    json.dumps({c: [{"slug": s, "party": w} for s, w in PARTIES[c]]
                for c in sorted(PARTIES, key=natural)},
               ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n{made} file(s) created, {kept} already there, "
      f"{len(PARTIES)} contracts x 2 parties")
