"""Couche de securite: prompt injection, operations interdites, cas sensibles.

Executee AVANT le retrieval. Priorite du comportement:
  1. Securite LLM (prompt injection / exfiltration)  -> REFUSE
  2. Operations interdites (hors perimetre strict)    -> REFUSE
  3. Cas sensibles (fraude / vol / compte compromis)  -> SENSITIVE

Les motifs sont volontairement bases sur des signaux clairs de danger ou
d'execution pour NE PAS capter les questions FAQ informatives
(ex: "comment faire opposition ?" reste une question FAQ).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.text_utils import normalize

# --- 1. Securite LLM / prompt injection -------------------------------------
PROMPT_INJECTION_PATTERNS = [
    r"ignore\w*\s+(?:tes|les|toutes?\s+tes|vos)?\s*(?:instructions|consignes|regles)",
    r"oublie\w*\s+(?:tes|les|vos)?\s*(?:instructions|consignes|regles)",
    r"(?:donne|montre|affiche|revele|divulgue|repete)\w*.{0,20}(?:prompt|instructions?\s+internes?|instructions?\s+syst)",
    r"prompt\s+syst",
    r"system\s+prompt",
    r"invente\w*\s+(?:une\s+)?(?:reponse|information)",
    r"repond\w*.{0,20}meme\s+si.{0,20}(?:pas\s+dans\s+la\s+faq|tu\s+n\s*as\s+pas|hors\s+faq)",
    r"repond\w*\s+hors\s+faq",
    r"(?:fais\s+comme\s+si|imagine\s+que|tu\s+es\s+maintenant|pretends?|joue\s+le\s+role).{0,30}(?:conseiller|banquier|agent|assistant\s+sans)",
    r"(?:contourn|bypass|desactive|desactiver).{0,20}(?:securite|regles|garde)",
    r"(?:tes|vos|les|quelles?\s+sont\s+tes|donne\w*\s+moi\s+tes)\s*(?:failles?|faiblesses?|vulnerabilit)",
    r"faille.{0,15}securit",
    r"(?:donnees?|logs?|traces?)\s+internes?",
    r"exfiltr",
    r"jailbreak",
    r"developer\s+mode",
    r"\bdan\s+mode\b",
]

# --- 2. Operations interdites (hors perimetre strict) -----------------------
# Motifs cibles: demande de donnee client OU execution reelle a la 1ere personne.
# Ils sont volontairement etroits pour NE PAS capter les questions FAQ
# informatives (ex: "Ou puis-je effectuer un virement ?" -> reste une FAQ).
PROHIBITED_PATTERNS = [
    # Consultation de solde (demande explicite de voir SON solde)
    (r"(?:quel\s+est|c\s+est\s+quoi|affiche\w*|montre\w*|donne\w*|voir|consulter|connaitre|verifier)\s+(?:moi\s+)?(?:mon|le)\s+solde", "consultation_solde"),
    (r"mon\s+solde\s*\??\s*$", "consultation_solde"),
    (r"combien\s+(?:j\s*ai|il\s+me\s+reste|d\s+argent\s+j)", "consultation_solde"),
    (r"chhal\s+3andi", "consultation_solde"),
    # Donnees client (afficher SES informations de compte)
    (r"(?:mes|mon)\s+(?:donnees|informations?|coordonnees)\s+(?:de\s+)?(?:mon\s+)?compte", "donnees_client"),
    (r"(?:donne|affiche|montre)\w*.{0,20}(?:mes|les)\s+informations?\s+de\s+mon\s+compte", "donnees_client"),
    (r"(?:donne|affiche|montre)\w*.{0,25}numero\s+(?:de\s+)?(?:ma\s+)?carte", "donnees_client"),
    (r"numero\s+(?:de\s+)?carte\s+de\s+mon\s+compte", "donnees_client"),
    # Virement / operation reelle (commande imperative ou avec montant)
    (r"(?:fais|faites)\s+(?:moi\s+)?un\s+virement", "operation_virement"),
    (r"(?:je\s+veux|je\s+voudrais|je\s+souhaite)\s+(?:faire|effectuer|passer)\s+un\s+virement\s+de\s+\d", "operation_virement"),
    (r"vire\w*\s+\d", "operation_virement"),
    (r"envoie\w*\s+\d+\s*(?:dh|dirham|mad|euros?)", "operation_virement"),
    # Modification de plafond reelle (commande imperative "mon plafond")
    (r"(?:modifie\w*|change\w*|augmente\w*|baisse\w*|reduis\w*)\s+(?:moi\s+)?mon\s+plafond", "modif_plafond"),
    # Acces / connexion au compte
    (r"(?:connecte|connectez|log)\w*.{0,15}(?:a\s+)?mon\s+compte", "acces_compte"),
    (r"acces\s+a\s+mon\s+compte", "acces_compte"),
    # Decision de credit / eligibilite personnelle (avec montant/produit credit)
    (r"suis\s*[- ]?je\s+eligible.{0,40}(?:credit|pret|financement|\d)", "eligibilite"),
    (r"est\s*[- ]?ce\s+que\s+je\s+suis\s+eligible.{0,40}(?:credit|pret|financement|\d)", "eligibilite"),
    (r"(?:quel|quelle)\s+(?:placement|produit|credit).{0,30}(?:me\s+conseille|pour\s+moi|gagner\s+le\s+plus|le\s+plus\s+rentable)", "conseil_perso"),
    (r"conseil\w*.{0,20}(?:financier\s+personnalise|pour\s+mon\s+argent)", "conseil_perso"),
]

# Categories "operation" qui restent legitimes si posees comme question FAQ
# (comment / ou / puis-je ...) -> on ne les bloque pas dans ce cas.
_HOWTO_SUPPRESSED = {"modif_plafond", "consultation_solde", "acces_compte"}
_HOWTO_RE = re.compile(
    r"^(?:comment|ou\b|quand|pourquoi|est\s*-?\s*ce|est\s*-?\s*il|puis\s*-?\s*je|"
    r"peut\s*-?\s*on|quelle?\s+est|c\s+est\s+quoi|combien\s+de\s+temps)")

# --- 3. Cas sensibles (fraude / securite du compte) -------------------------
SENSITIVE_PATTERNS = [
    (r"\bfraud", "fraude"),
    (r"phishing|hameconnage|hammeconnage", "phishing"),
    (r"arnaqu|escroqu|scam", "arnaque"),
    (r"carte.{0,15}(?:vol|perd)|(?:vol|perd)\w*.{0,15}(?:ma\s+)?carte", "carte_perdue_volee"),
    (r"tserqat|sreqat|msroq|twedar", "carte_perdue_volee"),
    (r"compte.{0,15}(?:compromis|pirate|hacke|usurp)", "compte_compromis"),
    (r"(?:je\s+me\s+suis\s+fait|on\s+m\s+a)\s+(?:pirat|arnaqu|vol)", "compte_compromis"),
    (r"(?:paiement|transaction|prelevement|debit|operation).{0,20}(?:suspect|inconnu|non\s+autorise|que\s+je\s+n\s*ai\s+pas)", "paiement_suspect"),
    (r"(?:fais|faites)\s+opposition", "opposition_urgente"),
    (r"(?:bloque|bloquez)\s+(?:ma|la)\s+carte", "opposition_urgente"),
    (r"otp.{0,20}(?:pas\s+demande|non\s+demande|recu\s+sans)", "otp_suspect"),
]

_INJ_RE = [re.compile(p) for p in PROMPT_INJECTION_PATTERNS]
_PROH_RE = [(re.compile(p), tag) for p, tag in PROHIBITED_PATTERNS]
_SENS_RE = [(re.compile(p), tag) for p, tag in SENSITIVE_PATTERNS]


@dataclass
class GuardrailResult:
    triggered: bool
    status: Optional[str] = None            # REFUSE | SENSITIVE
    flags: List[str] = field(default_factory=list)
    message: Optional[str] = None
    orientation: Optional[str] = None
    debug_reason: Optional[str] = None


# Messages professionnels (vouvoiement, sans emoji, < 120 mots).
MSG_INJECTION = (
    "Je ne peux pas traiter cette demande. Je reponds uniquement aux questions "
    "couvertes par la FAQ officielle et je ne peux pas modifier mon fonctionnement "
    "ni divulguer d'informations internes. Posez-moi votre question et je verifie "
    "ma base de connaissance."
)

MSG_PROHIBITED = {
    "consultation_solde": "Pour consulter votre solde, connectez-vous a l'application Simple. Je ne peux pas acceder a vos donnees personnelles.",
    "donnees_client": "Je ne peux pas acceder ni afficher vos donnees personnelles. Retrouvez ces informations dans l'application Simple, rubrique correspondante.",
    "operation_virement": "Je ne peux pas realiser d'operation bancaire. Effectuez votre virement depuis l'application Simple ou rapprochez-vous de votre agence.",
    "modif_plafond": "Je ne peux pas modifier vos plafonds. Vous pouvez le faire depuis l'application Simple, ou contacter votre conseiller.",
    "acces_compte": "Je ne peux pas acceder a votre compte. Connectez-vous a l'application Simple avec vos identifiants habituels.",
    "eligibilite": "Je ne peux pas me prononcer sur votre eligibilite. Une etude personnalisee est realisee par votre agence ou votre conseiller.",
    "conseil_perso": "Je ne peux pas fournir de conseil financier personnalise. Votre conseiller peut vous accompagner selon votre situation.",
}

MSG_SENSITIVE = (
    "Pour votre securite, ne communiquez jamais votre mot de passe, code PIN, "
    "code de confirmation (OTP) ni le numero complet de votre carte. "
    "Si vous suspectez une fraude, une perte ou un vol, agissez immediatement "
    "depuis l'application Simple (blocage/opposition) et contactez le service "
    "client au plus vite."
)


def check_prompt_injection(text: str) -> Optional[GuardrailResult]:
    norm = normalize(text)
    for rx in _INJ_RE:
        if rx.search(norm):
            return GuardrailResult(
                triggered=True,
                status="REFUSE",
                flags=["SECURITY_PROMPT_INJECTION"],
                message=MSG_INJECTION,
                orientation="Refus securise",
                debug_reason=f"prompt_injection: motif '{rx.pattern[:40]}'",
            )
    return None


def check_prohibited(text: str) -> Optional[GuardrailResult]:
    norm = normalize(text)
    howto = bool(_HOWTO_RE.match(norm))
    for rx, tag in _PROH_RE:
        if howto and tag in _HOWTO_SUPPRESSED:
            continue  # question "comment/ou modifier le plafond" -> reste FAQ
        if rx.search(norm):
            return GuardrailResult(
                triggered=True,
                status="REFUSE",
                flags=[f"PROHIBITED:{tag}"],
                message=MSG_PROHIBITED.get(tag, MSG_PROHIBITED["donnees_client"]),
                orientation="Application Simple / Agence / Service client",
                debug_reason=f"operation_interdite: {tag}",
            )
    return None


def check_sensitive(text: str) -> Optional[GuardrailResult]:
    norm = normalize(text)
    for rx, tag in _SENS_RE:
        if rx.search(norm):
            return GuardrailResult(
                triggered=True,
                status="SENSITIVE",
                flags=[f"SENSITIVE:{tag}"],
                message=MSG_SENSITIVE,
                orientation="Escalader vers canal humain / Application Simple",
                debug_reason=f"cas_sensible: {tag}",
            )
    return None


def run_input_guardrails(text: str) -> GuardrailResult:
    """Applique les garde-fous d'entree dans l'ordre de priorite."""
    for check in (check_prompt_injection, check_prohibited, check_sensitive):
        res = check(text)
        if res is not None:
            return res
    return GuardrailResult(triggered=False)


# --- Garde-fou de sortie (anti-fuite) ---------------------------------------
_LEAK_PATTERNS = [
    re.compile(r"prompt\s+syst", re.IGNORECASE),
    re.compile(r"voici\s+mes\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
]


def check_output_leak(answer: str) -> bool:
    """True si la reponse semble divulguer le prompt/instructions internes."""
    return any(rx.search(answer or "") for rx in _LEAK_PATTERNS)
