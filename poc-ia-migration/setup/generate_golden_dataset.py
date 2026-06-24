"""
Generates test data for the POC:
  - tests/golden_dataset.csv    : source CLIENTS table (30 rows, mixed statuts)
  - tests/ref_statut.csv        : reference REF_STATUT table
  - tests/expected_output.csv   : expected DIM_CLIENTS after all transformations

BATCH_DATE = 2026-01-01
SYSDATE reference for AGE = 2026-06-24 (fixed for reproducibility)
Filter: excludes rows where STATUT_CODE = 'I'
"""
import os
import pandas as pd
from datetime import datetime, date

BATCH_DATE = "2026-01-01"
SYSDATE = datetime(2026, 6, 24)

REF_STATUT = [
    {"CODE": "A", "LIBELLE": "Actif",    "LIBELLE_COURT": "ACT"},
    {"CODE": "I", "LIBELLE": "Inactif",  "LIBELLE_COURT": "INA"},
    {"CODE": "S", "LIBELLE": "Suspendu", "LIBELLE_COURT": "SUS"},
    {"CODE": "P", "LIBELLE": "Prospect", "LIBELLE_COURT": "PRO"},
]

RAW_CLIENTS = [
    # id, nom (with spaces/case noise), prenom, date_naissance, statut, date_creation, email, segment, pays, date_maj
    (1,  "  dupont  ",   "jean      ",  "1980-03-15", "A", "2020-01-10", "  Jean.Dupont@Mail.FR  ", "PREM", "FR", "2026-01-05"),
    (2,  "MARTIN",       "Sophie",      "1992-07-22", "A", "2021-03-01", "sophie.martin@gmail.com", "STAN", "FR", "2026-01-03"),
    (3,  "  BEN ALI",    "  Karim  ",   "1975-11-30", "I", "2019-06-15", "k.benali@outlook.com",    "PREM", "DZ", "2025-12-20"),
    (4,  "GARCIA",       "Maria",       "1988-05-10", "A", "2022-04-20", "Maria.Garcia@Yahoo.ES",   "GOLD", "ES", "2026-01-08"),
    (5,  "  SCHMIDT  ",  "Hans",        "1965-09-01", "S", "2018-11-11", "hans.schmidt@web.de",     "STAN", "DE", "2026-01-02"),
    (6,  "NGUYEN",       "Lan",         "2000-02-28", "A", "2023-01-15", "LAN.NGUYEN@corp.vn",      "STAN", "VN", "2026-01-07"),
    (7,  "BERNARD",      "  Claire ",   "1990-12-05", "I", "2020-09-30", "claire.bernard@free.fr",  "STAN", "FR", "2025-11-15"),
    (8,  "  LEFEBVRE",   "Pierre",      "1983-04-17", "A", "2021-07-22", "p.lefebvre@orange.fr",    "PREM", "FR", "2026-01-09"),
    (9,  "MOREAU",       "Anne-Marie",  "1970-08-25", "A", "2017-02-14", "anne.moreau@sfr.fr",      "GOLD", "FR", "2026-01-06"),
    (10, "SIMON",        "Thomas",      "1995-06-14", "I", "2022-10-01", "t.simon@gmail.com",       "STAN", "FR", "2025-10-30"),
    (11, "  LAURENT  ",  "Isabelle",    "1978-01-20", "A", "2019-05-05", "i.laurent@hotmail.fr",    "PREM", "FR", "2026-01-04"),
    (12, "MICHEL",       "François",    "1986-10-03", "S", "2020-12-01", "f.michel@gmail.com",      "STAN", "FR", "2026-01-01"),
    (13, "LEROY",        "Nathalie",    "1993-03-29", "A", "2023-06-10", "n.leroy@yahoo.fr",        "STAN", "BE", "2026-01-10"),
    (14, "ROUX",         "David",       "1968-07-07", "A", "2016-08-20", "david.roux@wanadoo.fr",   "GOLD", "FR", "2026-01-05"),
    (15, "DAVID",        "Céline",      "1997-02-11", "I", "2023-09-15", "celine.david@gmail.com",  "STAN", "FR", "2025-09-20"),
    (16, "  BERTRAND ",  "Julien",      "1984-11-22", "A", "2021-01-30", "j.bertrand@sfr.fr",       "STAN", "FR", "2026-01-08"),
    (17, "MOREL",        "Christine",   "1972-06-18", "A", "2018-04-12", "c.morel@orange.fr",       "PREM", "FR", "2026-01-03"),
    (18, "FOURNIER",     "Marc",        "1960-09-09", "S", "2015-11-25", "m.fournier@free.fr",      "GOLD", "FR", "2026-01-07"),
    (19, "GIRARD",       "Lucie",       "2001-04-04", "A", "2024-02-01", "lucie.girard@gmail.com",  "STAN", "FR", "2026-01-09"),
    (20, "BONNET",       "Olivier",     "1979-12-31", "I", "2020-07-08", "o.bonnet@hotmail.fr",     "STAN", "FR", "2025-08-10"),
    (21, "  MARTINEZ  ", "Elena",       "1991-08-16", "A", "2022-03-25", "ELENA.martinez@gmail.COM","STAN", "ES", "2026-01-06"),
    (22, "LAMBERT",      "Sébastien",   "1987-05-27", "A", "2021-09-14", "s.lambert@outlook.fr",    "PREM", "FR", "2026-01-10"),
    (23, "FONTAINE",     "Monique",     "1958-02-03", "A", "2014-06-30", "m.fontaine@wanadoo.fr",   "GOLD", "FR", "2026-01-02"),
    (24, "ROUSSEAU",     "Antoine",     "1999-10-10", "I", "2023-11-20", "a.rousseau@gmail.com",    "STAN", "FR", "2025-07-05"),
    (25, "  VINCENT  ",  "Patricia",    "1966-03-22", "A", "2017-10-18", "p.vincent@free.fr",       "PREM", "FR", "2026-01-04"),
    (26, "MULLER",       "Klaus",       "1982-07-14", "S", "2020-05-16", "k.muller@gmx.de",         "STAN", "DE", "2026-01-08"),
    (27, "LECOMTE",      "Valérie",     "1976-11-08", "A", "2019-03-07", "v.lecomte@sfr.fr",        "PREM", "FR", "2026-01-05"),
    (28, "FERNANDEZ",    "Carlos",      "1989-04-25", "A", "2022-08-19", "carlos.fz@gmail.com",     "STAN", "ES", "2026-01-07"),
    (29, "HENRY",        "Brigitte",    "1963-01-17", "I", "2016-12-03", "b.henry@orange.fr",       "GOLD", "FR", "2025-06-12"),
    (30, "  BLANC  ",    "Éric",        "1994-09-06", "A", "2023-04-11", "eric.blanc@gmail.com",    "STAN", "FR", "2026-01-09"),
]


def calc_age(birth_date: date, ref: datetime) -> int:
    age = ref.year - birth_date.year
    if (ref.month, ref.day) < (birth_date.month, birth_date.day):
        age -= 1
    return age


def main():
    os.makedirs("tests", exist_ok=True)

    ref_statut_df = pd.DataFrame(REF_STATUT)
    ref_statut_df.to_csv("tests/ref_statut.csv", index=False)
    print("[OK] tests/ref_statut.csv generated")

    columns = ["CLIENT_ID","NOM","PRENOM","DATE_NAISSANCE","STATUT_CODE",
               "DATE_CREATION","EMAIL","SEGMENT_CODE","PAYS_CODE","DATE_MAJ"]
    golden = pd.DataFrame(RAW_CLIENTS, columns=columns)
    golden.to_csv("tests/golden_dataset.csv", index=False)
    print(f"[OK] tests/golden_dataset.csv generated ({len(golden)} rows)")

    ref_map = {row["CODE"]: row["LIBELLE"] for row in REF_STATUT}

    expected_rows = []
    for _, row in golden.iterrows():
        if row["STATUT_CODE"] == "I":
            continue
        birth = datetime.strptime(row["DATE_NAISSANCE"], "%Y-%m-%d").date()
        age = calc_age(birth, SYSDATE)
        expected_rows.append({
            "CLIENT_ID":      row["CLIENT_ID"],
            "NOM_CLEAN":      row["NOM"].strip().upper(),
            "PRENOM_CLEAN":   row["PRENOM"].strip().upper(),
            "AGE":            age,
            "STATUT_CODE":    row["STATUT_CODE"],
            "STATUT_LIBELLE": ref_map.get(row["STATUT_CODE"], ""),
            "EMAIL_LOWER":    row["EMAIL"].strip().lower(),
            "SEGMENT_CODE":   row["SEGMENT_CODE"],
            "PAYS_CODE":      row["PAYS_CODE"],
            "DATE_CREATION":  row["DATE_CREATION"],
            "BATCH_DATE":     BATCH_DATE,
        })

    expected_df = pd.DataFrame(expected_rows)
    expected_df.to_csv("tests/expected_output.csv", index=False)
    print(f"[OK] tests/expected_output.csv generated ({len(expected_df)} rows after filter)")
    print(f"     Filtered out: {len(golden) - len(expected_df)} inactive clients")


if __name__ == "__main__":
    main()
