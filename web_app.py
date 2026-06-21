import streamlit as st
import json
import os

# 設定手機版網頁全螢幕顯示
st.set_page_config(page_title="📱 國考刷題", layout="centered", initial_sidebar_state="collapsed")

# ==========================================
# 注入自訂 CSS 來打造「手機 App 原生感」排版
# ==========================================
st.markdown("""
    <style>
    /* 隱藏預設的頂部選單、底部浮水印、頂部工具列，把整個畫面讓給內容本身 */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden; height: 0;}
    [data-testid="stToolbar"] {visibility: hidden; height: 0;}

    /* 關閉下拉刷新的彈跳效果，更有原生 App 的觸感 */
    html, body {
        overscroll-behavior-y: contain;
    }

    /* 讓整個網頁的最下方多留一點空白，避免題目內容被底部按鈕蓋住 */
    .block-container {
        padding-top: 1.2rem;
        padding-bottom: 112px;
        max-width: 600px;
    }

    /* 題目卡片：圓角、陰影，內容跟周圍留白更舒服 */
    .st-key-question_card {
        border-radius: 16px !important;
        padding: 18px !important;
        box-shadow: 0px 2px 10px rgba(0,0,0,0.06);
    }
    .st-key-question_card img {
        border-radius: 12px;
    }

    /* 跳題用的摺疊區塊，稍微收斂一點視覺權重 */
    [data-testid="stExpander"] {
        border-radius: 12px;
    }

    /* 進度條圓角化 */
    div[data-testid="stProgress"] > div > div {
        border-radius: 8px;
    }

    /* 一般按鈕也加大一點，方便手指點擊 */
    div[data-testid="stButton"] button {
        border-radius: 10px;
        min-height: 44px;
    }

    /* 將放置主要操作按鈕的容器，固定在螢幕正下方(用 container(key=) 精準鎖定，
       不會誤鎖到頁面上其他的 columns 排版) */
    .st-key-bottom_nav {
        position: fixed;
        left: 0;
        right: 0;
        bottom: 0;
        background-color: #FFFFFF;
        padding: 10px 14px calc(14px + env(safe-area-inset-bottom)) 14px;
        z-index: 999;
        border-top: 1px solid #E3E6F0;
        box-shadow: 0px -4px 12px rgba(0,0,0,0.05);
    }
    .st-key-bottom_nav button {
        height: 52px !important;
        font-size: 16px !important;
        font-weight: 600 !important;
        border-radius: 12px !important;
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
total = len(questions)
q = questions[idx]

# 進度條
st.progress((idx + 1) / total, text=f"進度：第 {idx + 1} / {total} 題")

# 跳題功能(收合在摺疊區塊裡，不佔用主畫面空間，需要才展開)
with st.expander("🔢 跳至指定題號"):
    jc1, jc2 = st.columns([3, 1])
    with jc1:
        jump_target = st.number_input("題號", min_value=1, max_value=total,
                                       value=idx + 1, label_visibility="collapsed")
    with jc2:
        if st.button("前往", width='stretch'):
            st.session_state.q_idx = int(jump_target) - 1
            st.session_state.show_ans = False
            st.rerun()

# ==========================================
# 題目卡片區
# ==========================================
with st.container(border=True, key="question_card"):
    st.markdown(f"#### Q{q['id']}")
    st.write(q['text'])

    ans_val = q.get('answer', 'N/A')
    is_mc = bool(q.get("options")) and len(ans_val) < 5

    # 選項顯示區 (按下解答後，正確選項原地變綠並加上 ✅)
    for k, v in q.get("options", {}).items():
        if not v:
            continue
        if st.session_state.show_ans and is_mc and k in ans_val:
            st.markdown(f"**({k})** :green[**✅ {v}**]　👈 正確解答")
        else:
            st.markdown(f"**({k})** {v}")

    # 若是長篇簡答題(非選擇題)，則另外在下方顯示完整答案
    if st.session_state.show_ans and not is_mc:
        st.success(f"**標準答案：**\n\n{ans_val}")

    # 顯示圖片 (如果有)
    if "image" in q:
        img_path = os.path.join("local_data", q["image"])
        if os.path.exists(img_path):
            st.image(img_path, width='stretch')

st.write("")  # 跟底部固定按鈕保留一點呼吸空間，避免緊貼

# ==========================================
# 底部固定按鈕區 (用 container(key="bottom_nav") 精準鎖定固定，
# 不會誤把頁面上其他的 st.columns() 排版也一起鎖住)
# ==========================================
with st.container(key="bottom_nav"):
    col1, col2, col3 = st.columns(3, gap="small")

    with col1:
        if st.button("⬅️ 上一題", width='stretch', disabled=(idx == 0)):
            st.session_state.q_idx -= 1
            st.session_state.show_ans = False
            st.rerun()

    with col2:
        ans_label = "🙈 收起解答" if st.session_state.show_ans else "💡 看解答"
        if st.button(ans_label, width='stretch', type="primary"):
            st.session_state.show_ans = not st.session_state.show_ans
            st.rerun()

    with col3:
        if st.button("下一題 ➡️", width='stretch', disabled=(idx == total - 1)):
            st.session_state.q_idx += 1
            st.session_state.show_ans = False
            st.rerun()
