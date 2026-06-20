import streamlit as st
import json
import os

# 設定手機版網頁全螢幕顯示
st.set_page_config(page_title="📱 國考刷題", layout="centered")

# 讀取電腦端處理好的 JSON 題庫
@st.cache_data
def load_data():
    with open("local_data/questions.json", "r", encoding="utf-8") as f:
        return json.load(f)

data = load_data()
decks = list(data.get("decks", {}).keys())

if not decks:
    st.warning("目前沒有題庫，請先在電腦端使用轉換工具匯入 PDF！")
    st.stop()

# 頂部題庫選擇
selected_deck = st.selectbox("📂 選擇題庫", decks)
questions = data["decks"][selected_deck]["questions"]

# 題號狀態管理
if "q_idx" not in st.session_state or st.session_state.get("last_deck") != selected_deck:
    st.session_state.q_idx = 0
    st.session_state.last_deck = selected_deck
    st.session_state.show_ans = False

idx = st.session_state.q_idx
q = questions[idx]

# 進度條與題目顯示
st.progress((idx + 1) / len(questions), text=f"進度: {idx + 1} / {len(questions)}")
st.markdown(f"### Q{q['id']}")
st.write(q['text'])

# 顯示選項
for k, v in q.get("options", {}).items():
    if v: st.write(f"**({k})** {v}")

# 顯示圖片 (如果有)
if "image" in q:
    img_path = os.path.join("local_data", q["image"])
    if os.path.exists(img_path):
        st.image(img_path, use_container_width=True)

# 操作按鈕區
st.divider()
col1, col2, col3 = st.columns(3)

if col1.button("⬅️ 上一題", use_container_width=True) and idx > 0:
    st.session_state.q_idx -= 1
    st.session_state.show_ans = False
    st.rerun()

if col2.button("💡 看解答", use_container_width=True):
    st.session_state.show_ans = not st.session_state.show_ans
    st.rerun()

if col3.button("下一題 ➡️", use_container_width=True) and idx < len(questions) - 1:
    st.session_state.q_idx += 1
    st.session_state.show_ans = False
    st.rerun()

# 顯示解答邏輯
if st.session_state.show_ans:
    st.success(f"**標準答案： {q.get('answer', 'N/A')}**")