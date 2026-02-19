from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st


def main() -> None:
    st.set_page_config(
        page_title="Dynsight GUI",
        layout="wide",
        initial_sidebar_state="auto",
    )
    st.title("Dynsight Graphical User Interface")


def launch() -> None:
    from streamlit.web import cli as stcli

    script_path = Path(__file__).resolve()
    sys.argv = ["streamlit", "run", str(script_path)]
    sys.exit(stcli.main())


# Backward-compatible alias for older local installs.
run = launch


if __name__ == "__main__":
    main()
