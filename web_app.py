import streamlit as st
import json
import os

# 設定手機版網頁全螢幕顯示
st.set_page_config(page_title="📱 國考刷題", layout="centered")

# ==========================================
# 注入自訂 CSS 來打造「手機 App 原生感」排版
# ==========================================
st.markdown("""
    <style>
    /* 隱藏預設的頂部選單與底部浮水印 */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* 讓整個網頁的最下方多留一點空白，避免題目內容被底部按鈕蓋住 */
    .block-container {
        padding-bottom: 90px;
    }

    /* 將放置按鈕的欄位 (stHorizontalBlock) 強制固定在螢幕正下方 */
    [data-testid="stHorizontalBlock"] {
        position: fixed;
        bottom: 0;
        left: 0;
        right: 0;
        background-color: #FFFFFF;
        padding: 15px 20px 25px 20px;
        z-index: 999;
        border-top: 1px solid #E3E6F0;
        box-shadow: 0px -4px 12px rgba(0,0,0,0.05);
    }
    </style>
""", unsafe_allow_html=True)

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

# 題號與狀態管理
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

# ==========================================
# 選項顯示區 (按下解答後原地變紅)
# ==========================================
ans_val = q.get('answer', 'N/A')

for k, v in q.get("options", {}).items():
    if v:
        # 如果使用者按了「解答」，且這個選項字母包含在正確答案內
        if st.session_state.show_ans and k in ans_val and len(ans_val) < 5:
            # 使用 :red[] 語法將文字變紅，並加上指引符號
            st.markdown(f"**({k})** :red[**{v}**] 👈 **正確解答**")
        else:
            # 正常顯示
            st.write(f"**({k})** {v}")

# 若是長篇簡答題(非選擇題)，則另外在下方顯示完整答案
if st.session_state.show_ans and len(ans_val) >= 5:
    st.info(f"**標準答案：**\n{ans_val}")

# 顯示圖片 (如果有)
if "image" in q:
    img_path = os.path.join("local_data", q["image"])
    if os.path.exists(img_path):
        st.image(img_path, use_container_width=True)

# ==========================================
# 底部固定按鈕區 (固定在同一行)
# ==========================================
col1, col2, col3 = st.columns(3)

with col1:
    if st.button("⬅️ 上一題", use_container_width=True):
        if idx > 0:
            st.session_state.q_idx -= 1
            st.session_state.show_ans = False
            st.rerun() # 強制刷新畫面

with col2:
    if st.button("💡 解答", use_container_width=True):
        st.session_state.show_ans = not st.session_state.show_ans
        st.rerun()

with col3:
    if st.button("下一題 ➡️", use_container_width=True):
        if idx < len(questions) - 1:
            st.session_state.q_idx += 1
            st.session_state.show_ans = False
            st.rerun()