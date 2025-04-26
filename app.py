import streamlit as st
from dotenv import load_dotenv
from pipeline import build_pipeline

load_dotenv()

st.set_page_config(page_title="Deep Research AI")
st.title("🔍 Deep Research AI Agent")

query = st.text_input("Enter your research query:")
if query:
    st.write("Processing...")
    pipeline = build_pipeline()
    result = pipeline.invoke({"query": query})
    st.markdown("### 📝 Response")
    st.write(result["response"])
