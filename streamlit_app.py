"""Interface Streamlit de test metier pour l'Assistant Virtuel Simple.

Lancement:
    streamlit run streamlit_app.py

Fonctions:
  - zone de chat
  - bouton "nouvelle session"
  - mode "debug recette" (Trace ID, statut, domaine, FAQ candidates, scores, raisons)
  - export des conversations
  - page dediee pour lancer un dataset de test
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

from app.chatbot import Assistant

st.set_page_config(page_title="Assistant Virtuel Simple - MVP", page_icon="A", layout="wide")

STATUS_COLORS = {
    "ANSWER": "#1a7f37", "ESCALATE": "#9a6700", "SENSITIVE": "#bf3989",
    "REFUSE": "#cf222e", "OUT_OF_SCOPE": "#6e7781", "CLARIFY": "#0969da",
}


@st.cache_resource
def get_assistant() -> Assistant:
    return Assistant()


def new_session() -> str:
    return f"ui-{uuid.uuid4().hex[:8]}"


def init_state():
    if "session_id" not in st.session_state:
        st.session_state.session_id = new_session()
    if "messages" not in st.session_state:
        st.session_state.messages = []  # list of dicts {role, content, meta}


def render_chat():
    st.title("Assistant Virtuel Simple - MVP")
    st.caption("Reponses issues uniquement de la FAQ officielle. Source unique de verite.")

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        if st.button("Nouvelle session"):
            st.session_state.session_id = new_session()
            st.session_state.messages = []
            st.rerun()
    with col2:
        debug = st.toggle("Mode debug recette", value=False)
    with col3:
        st.text(f"Session: {st.session_state.session_id}")

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if debug and msg.get("meta"):
                render_debug(msg["meta"])

    prompt = st.chat_input("Posez votre question...")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        assistant = get_assistant()
        resp = assistant.answer(prompt, session_id=st.session_state.session_id, debug=True)
        with st.chat_message("assistant"):
            color = STATUS_COLORS.get(resp["status"], "#6e7781")
            st.markdown(
                f"<span style='background:{color};color:white;padding:2px 8px;"
                f"border-radius:6px;font-size:0.8em'>{resp['status']}</span>",
                unsafe_allow_html=True,
            )
            st.markdown(resp["answer"])
            if debug:
                render_debug(resp)
        st.session_state.messages.append({"role": "assistant", "content": resp["answer"], "meta": resp})

    if st.session_state.messages:
        export = json.dumps(
            {"session_id": st.session_state.session_id, "messages": st.session_state.messages},
            ensure_ascii=False, indent=2,
        )
        st.download_button("Exporter la conversation (JSON)", export,
                           file_name=f"conversation_{st.session_state.session_id}.json")


def render_debug(resp: dict):
    with st.expander("Details recette", expanded=True):
        c1, c2, c3 = st.columns(3)
        c1.metric("Confidence", f"{resp.get('confidence', 0):.3f}")
        c2.metric("Statut", resp.get("status", ""))
        c3.metric("Domaine", resp.get("domain") or "-")
        st.write(f"**Trace ID:** `{resp.get('trace_id','')}`")
        st.write(f"**Motif:** {resp.get('motif') or '-'} | **Sous-motif:** {resp.get('sous_motif') or '-'}")
        st.write(f"**Orientation:** {resp.get('orientation') or '-'}")
        st.write(f"**Raison:** {resp.get('debug_reason','')}")
        if resp.get("guardrail_flags"):
            st.write(f"**Garde-fous:** {', '.join(resp['guardrail_flags'])}")
        cands = resp.get("debug", {}).get("candidates") or resp.get("sources", [])
        if cands:
            st.write("**FAQ candidates:**")
            st.dataframe(pd.DataFrame(cands), use_container_width=True, hide_index=True)


def render_recipe_page():
    st.title("Recette - Dataset de test")
    st.caption("Executez un echantillon du dataset et visualisez les resultats.")

    dataset_path = Path("data/test_dataset.xlsx")
    manual_path = Path("data/manual_test_cases.xlsx")
    if not manual_path.exists():
        st.warning("Datasets absents. Lancez d'abord `python scripts/generate_dataset.py`.")
        return

    source = st.radio("Dataset", ["Cas manuels (interdits/sensibles/securite)", "Echantillon FAQ"])
    n = st.slider("Nombre de cas", 5, 100, 20)
    if st.button("Lancer la recette"):
        if source.startswith("Cas manuels"):
            df = pd.read_excel(manual_path).head(n)
        else:
            df = pd.read_excel(dataset_path).sample(min(n, 100), random_state=1)
        assistant = get_assistant()
        rows = []
        prog = st.progress(0.0)
        for i, (_, r) in enumerate(df.iterrows()):
            resp = assistant.answer(r["Question"], session_id=f"recette-ui-{i}")
            rows.append({
                "ID": r["ID"], "Type": r["Type de test"], "Question": r["Question"],
                "Attendu": r["Comportement attendu"], "Obtenu": resp["status"],
                "Confidence": resp["confidence"], "Trace ID": resp["trace_id"],
                "Réponse": resp["answer"][:120],
            })
            prog.progress((i + 1) / len(df))
        res = pd.DataFrame(rows)
        res["OK"] = res.apply(lambda x: x["Obtenu"] in str(x["Attendu"]).split("|"), axis=1)
        st.metric("Taux de conformite (statut)", f"{100*res['OK'].mean():.0f}%")
        st.dataframe(res, use_container_width=True, hide_index=True)
        st.download_button("Exporter les resultats (CSV)", res.to_csv(index=False),
                           file_name="recette_ui.csv")


def main():
    init_state()
    page = st.sidebar.radio("Navigation", ["Chat", "Recette / Dataset"])
    st.sidebar.markdown("---")
    st.sidebar.caption("MVP oriente recette, securite et non-hallucination.")
    a = get_assistant()
    st.sidebar.write(f"FAQ indexees: **{len(a.index.records)}**")
    st.sidebar.write(f"Index: `{a.index.version()}`")

    if page == "Chat":
        render_chat()
    else:
        render_recipe_page()


if __name__ == "__main__":
    main()
