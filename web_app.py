import streamlit as st
import streamlit.components.v1 as components
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

    /* 鎖死手機版面，禁止雙擊與雙指放大整個網頁 */
    html, body {
        overscroll-behavior-x: none;
        overscroll-behavior-y: auto;
        touch-action: pan-y; /* 鎖死縮放的關鍵 */
        -webkit-text-size-adjust: 100%;
    }

    /* 讓整個網頁的最下方多留一點空白，避免題目內容被底部按鈕蓋住 */
    .block-container {
        padding-top: 1.2rem;
        padding-bottom: 112px;
        max-width: 600px;
    }


    .st-key-question_card {
        background-color: var(--background-color);
        border: 1px solid var(--secondary-background-color);
        border-radius: 16px !important;
        padding: 18px !important;
        box-shadow: 0px 2px 12px rgba(0,0,0,0.15);
        animation: fadeIn 0.25s ease-out forwards;
        contain: none !important;
    }

    .st-key-question_card img {
        border-radius: 12px;
        cursor: zoom-in; /* 提示使用者可以點擊放大 */
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

    .st-key-bottom_nav {
        position: fixed;
        left: 0;
        right: 0;
        bottom: 0;
        background-color: var(--background-color);
        padding: 10px 14px calc(14px + env(safe-area-inset-bottom)) 14px;
        z-index: 999;
        border-top: 1px solid var(--secondary-background-color);
        box-shadow: 0px -4px 12px rgba(0,0,0,0.15);
    }
    .st-key-bottom_nav button {
        height: 52px !important;
        font-size: 18px !important;
        font-weight: 600 !important;
        border-radius: 12px !important;
    }

    /* 懸浮側邊換題按鈕 (透明、不擋視線) */
    .st-key-btn_prev, .st-key-btn_next {
        position: fixed !important;
        top: 50% !important;
        transform: translateY(-50%) !important;
        z-index: 990 !important;
        width: 32px !important;
        height: 80px !important;
        opacity: 0.3;
        transition: opacity 0.2s;
    }
    .st-key-btn_prev:active, .st-key-btn_next:active { opacity: 0.8; }
    .st-key-btn_prev { left: 0 !important; }
    .st-key-btn_next { right: 0 !important; }
    .st-key-btn_prev button, .st-key-btn_next button {
        width: 100% !important;
        height: 100% !important;
        padding: 0 !important;
        border-radius: 6px !important;
        background: var(--secondary-background-color) !important;
        border: none !important;
        font-size: 18px !important;
        color: var(--text-color) !important;
    }
    </style>
""", unsafe_allow_html=True)

# 確保字體大小狀態存在
if "font_size" not in st.session_state:
    st.session_state.font_size = 18

# 注入動態字體 CSS
st.markdown(f"""
    <style>
    .st-key-question_card * {{
        font-size: {st.session_state.font_size}px !important;
        line-height: 1.6 !important;
    }}
    </style>
""", unsafe_allow_html=True)

# 讀取電腦端處理好的 JSON 題庫
# 讀取電腦端處理好的 JSON 題庫 (改為掃描所有子資料夾)
@st.cache_data
def load_data():
    combined_data = {"decks": {}}
    base_dir = "local_data"
    
    if os.path.exists(base_dir):
        for item in os.listdir(base_dir):
            sub_dir = os.path.join(base_dir, item)
            if os.path.isdir(sub_dir):
                q_json = os.path.join(sub_dir, "questions.json")
                if os.path.exists(q_json):
                    with open(q_json, "r", encoding="utf-8") as f:
                        deck_info = json.load(f)
                        deck_name = deck_info.get("metadata", {}).get("deck_name", item)
                        combined_data["decks"][deck_name] = deck_info
                        
    return combined_data

data = load_data()
decks = list(data.get("decks", {}).keys())

if not decks:
    st.warning("目前沒有題庫，請先在電腦端使用轉換工具匯入 PDF！")
    st.stop()

# 頂部題庫選擇
# --- [新增] 讀取網址列記憶 (讓瀏覽器記住進度) ---
url_deck = st.query_params.get("deck", None)
url_q = st.query_params.get("q", None)

# 頂部題庫選擇
default_deck_idx = decks.index(url_deck) if url_deck in decks else 0
selected_deck = st.selectbox("📂 選擇題庫", decks, index=default_deck_idx)
questions = data["decks"][selected_deck]["questions"]

# 題號與狀態管理
if "q_idx" not in st.session_state or st.session_state.get("last_deck") != selected_deck:
    init_idx = 0
    # 如果網址列有題號紀錄，且選中的題庫一致，就恢復該題號
    if url_q and url_q.isdigit() and selected_deck == url_deck:
        init_idx = int(url_q)
        if init_idx >= len(questions): init_idx = 0
        
    st.session_state.q_idx = init_idx
    st.session_state.last_deck = selected_deck
    st.session_state.show_ans = False

idx = st.session_state.q_idx

# 每次渲染都將目前進度寫回網址列
st.query_params["deck"] = selected_deck
st.query_params["q"] = str(idx)

total = len(questions)
q = questions[idx]

# 進度條
st.progress((idx + 1) / total, text=f"進度：第 {idx + 1} / {total} 題")


with st.expander("⚙️ 顯示設定與跳題"):
    # 字體調整拉桿
    st.slider("調整字體大小", min_value=14, max_value=36, step=1, key="font_size")
    
    st.write("跳至指定題號：")
    jc1, jc2 = st.columns([3, 1])
    with jc1:
        jump_target = st.number_input("題號", min_value=1, max_value=total,
                                       value=idx + 1, label_visibility="collapsed")
    with jc2:
        # 修正警告：改用 width='stretch'
        if st.button("前往", width='stretch'):
            st.session_state.q_idx = int(jump_target) - 1
            st.session_state.show_ans = False
            st.rerun()

# ==========================================
# 題目卡片區
# ==========================================
# 【關鍵修復】：拿掉 border=True，因為它會強制觸發 overflow: hidden 綁死圖片！
with st.container(key="question_card"):
    st.markdown(f"#### Q{q['id']}")
    st.write(q['text'])

    ans_val = q.get('answer', 'N/A')
    is_mc = bool(q.get("options")) and len(ans_val) < 5

    for k, v in q.get("options", {}).items():
        if not v:
            continue
        if st.session_state.show_ans and is_mc and k in ans_val:
            st.markdown(f"**({k})** :green[**✅ {v}**]")
        else:
            st.markdown(f"**({k})** {v}")

    if st.session_state.show_ans and not is_mc:
        st.success(f"**標準答案：**\n\n{ans_val}")

    if "image" in q and q["image"]:
        safe_img_path = q["image"].replace("\\", "/").lstrip("/")
        img_path = os.path.join("local_data", safe_img_path)
        
        if os.path.exists(img_path):
            # 修正警告：改用 width='stretch'
            st.image(img_path, width='stretch')
        else:
            st.error(f"⚠️ 找不到圖片檔案：{img_path}")

st.write("")  # 跟底部固定按鈕保留一點呼吸空間，避免緊貼

# ==========================================
# 懸浮側邊換題按鈕 (左右兩側的 < >)
# ==========================================
if idx > 0:
    if st.button("❮", key="btn_prev"):
        st.session_state.q_idx -= 1
        st.session_state.show_ans = False
        st.rerun()

if idx < total - 1:
    if st.button("❯", key="btn_next"):
        st.session_state.q_idx += 1
        st.session_state.show_ans = False
        st.rerun()


# ==========================================
# 注入滑動監聽 JS (左右滑動換題)
# ==========================================
components.html("""
<script>
(function() {
    const doc = window.parent.document;

    // 【1. 鎖死手機縮放】
    let metaViewport = doc.querySelector('meta[name="viewport"]');
    if (metaViewport) {
        metaViewport.setAttribute('content', 'width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no');
    } else {
        let meta = doc.createElement('meta');
        meta.name = 'viewport';
        meta.content = 'width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no';
        doc.head.appendChild(meta);
    }

    // 【2. 防重複綁定】
    if (window.parent.mySwipeAttached) return;
    window.parent.mySwipeAttached = true;

    let tsX = 0, tsY = 0;

    doc.addEventListener('touchstart', e => {
        tsX = e.changedTouches[0].screenX;
        tsY = e.changedTouches[0].screenY;
    }, {passive: true});

    doc.addEventListener('touchend', e => {
        let teX = e.changedTouches[0].screenX;
        let teY = e.changedTouches[0].screenY;
        let dX = tsX - teX;
        let dY = Math.abs(tsY - teY);

        if (dY > Math.abs(dX) || dY > 60) return;

        let card = doc.querySelector('.st-key-question_card');
        let btns = Array.from(doc.querySelectorAll('button'));
        let nextBtn = btns.find(b => b.innerText.includes('❯'));
        let prevBtn = btns.find(b => b.innerText.includes('❮'));

        // 【防白畫面機制】：加入過渡動畫，並在 400 毫秒後強制清除 inline style，避免新題目維持隱形！
        function triggerSwipe(btn) {
            if(card) { 
                card.style.transition = 'opacity 0.2s, transform 0.2s';
                card.style.opacity = '0'; 
                card.style.transform = 'scale(0.95)';
                
                setTimeout(() => { 
                    card.style.opacity = ''; 
                    card.style.transform = ''; 
                    card.style.transition = '';
                }, 400); 
            }
            setTimeout(() => btn.click(), 50);
        }

        if (dX > 40 && nextBtn) { // 向左滑 (下一題)
            triggerSwipe(nextBtn);
        } else if (dX < -40 && prevBtn) { // 向右滑 (上一題)
            triggerSwipe(prevBtn);
        }
    }, {passive: true});
})();
</script>
""", height=0, width=0)

# ==========================================
# 底部固定按鈕區 (只保留大顆解答按鈕)
# ==========================================
with st.container(key="bottom_nav"):
    ans_label = "🙈 收起解答" if st.session_state.show_ans else "💡 看解答"
    if st.button(ans_label, width='stretch', type="primary"):
        st.session_state.show_ans = not st.session_state.show_ans
        st.rerun()