"""Aktien-Universen (Yahoo-Finance-Ticker).

Die Listen sind bewusst breit gefasst und enthalten viele Titel, die in den
letzten Jahren starke Einbrüche und/oder Erholungen erlebt haben. Nicht mehr
gelistete oder fehlerhafte Ticker werden beim Laden automatisch übersprungen.
"""

UNIVERSES: dict[str, list[str]] = {
    "DAX 40": [
        "ADS.DE", "AIR.DE", "ALV.DE", "BAS.DE", "BAYN.DE", "BEI.DE", "BMW.DE",
        "BNR.DE", "CBK.DE", "CON.DE", "DTG.DE", "DBK.DE", "DB1.DE", "DHL.DE",
        "DTE.DE", "EOAN.DE", "FRE.DE", "FME.DE", "HNR1.DE", "HEI.DE", "HEN3.DE",
        "IFX.DE", "MBG.DE", "MRK.DE", "MTX.DE", "MUV2.DE", "P911.DE", "PAH3.DE",
        "QIA.DE", "RHM.DE", "RWE.DE", "SAP.DE", "SRT3.DE", "SIE.DE", "ENR.DE",
        "SHL.DE", "SY1.DE", "VNA.DE", "VOW3.DE", "ZAL.DE", "G1A.DE",
    ],
    "MDAX / SDAX Auswahl": [
        "LHA.DE", "TUI1.DE", "TKA.DE", "EVK.DE", "KGX.DE", "PUM.DE", "HFG.DE",
        "LEG.DE", "AT1.DE", "GBF.DE", "SDF.DE", "NDX1.DE", "SZG.DE", "AIXA.DE",
        "JEN.DE", "WAF.DE", "FNTN.DE", "UTDI.DE", "1U1.DE", "BOSS.DE", "FPE3.DE",
        "TLX.DE", "HOT.DE", "EVT.DE", "S92.DE", "DUE.DE", "KBX.DE", "RAA.DE",
        "TEG.DE", "PSM.DE", "BC8.DE", "NEM.DE", "SHA0.DE", "GFT.DE", "AFX.DE",
        "SAX.DE", "KRN.DE", "WCH.DE", "STM.DE", "COK.DE", "DHER.DE", "TMV.DE",
        "SMHN.DE", "HAG.DE", "R3NK.DE", "AOF.DE", "VBK.DE", "PBB.DE",
    ],
    "Europa Auswahl": [
        "ASML.AS", "PHIA.AS", "UBI.PA", "KER.PA", "WLN.PA", "RNO.PA", "STLAM.MI",
        "NOKIA.HE", "ERIC-B.ST", "VOD.L", "BP.L", "BARC.L", "RR.L", "NOVO-B.CO",
        "ORSTED.CO", "VWS.CO", "UBSG.SW", "NESN.SW", "ROG.SW", "UCG.MI", "ISP.MI",
        "ENI.MI", "TIT.MI", "SAN.MC", "BBVA.MC", "IAG.L", "AF.PA", "ACA.PA",
        "BNP.PA", "SAN.PA", "OR.PA", "MC.PA", "CS.PA", "STMPA.PA", "ABI.BR",
        "HEIA.AS", "AD.AS", "INGA.AS", "PRX.AS", "TEP.PA", "ALO.PA", "VIV.PA",
        "BT-A.L", "GSK.L", "AZN.L", "LLOY.L", "OCDO.L", "BURBY.L",
    ],
    "USA Auswahl": [
        "INTC", "BA", "PFE", "NKE", "PYPL", "DIS", "F", "GM", "CCL", "NCLH",
        "RCL", "AAL", "UAL", "DAL", "MRNA", "CVS", "SBUX", "TGT", "EL", "MMM",
        "GE", "AMD", "META", "NFLX", "UBER", "PTON", "SNAP", "LYFT", "M", "KSS",
        "WBD", "VZ", "T", "BIIB", "LULU", "ETSY", "ZM", "ROKU", "AAP", "BBY",
        "DG", "DLTR", "NEM", "CLF", "FCX", "AA", "MU", "WDC", "STX", "AFRM",
        "XYZ", "HOOD", "PLTR", "CHWY", "RIVN", "LCID", "NIO", "BABA", "UNH",
        "MDT", "WBA", "XOM", "OXY", "CMCSA", "PARA", "IBM", "CSCO", "C", "WFC",
        "SCHW", "TSLA", "BIDU", "JD", "ADBE", "CRM", "SMCI", "ENPH", "FSLR",
    ],
}

DEFAULT_UNIVERSES = ["DAX 40", "MDAX / SDAX Auswahl"]


def build_universe(selected: list[str], custom: str = "") -> list[str]:
    """Kombiniert gewählte Universen und eigene, kommagetrennte Ticker."""
    tickers: list[str] = []
    for name in selected:
        tickers.extend(UNIVERSES.get(name, []))
    for raw in custom.replace(";", ",").replace("\n", ",").split(","):
        t = raw.strip().upper()
        if t:
            tickers.append(t)
    # Reihenfolge erhalten, Duplikate entfernen
    return list(dict.fromkeys(tickers))
