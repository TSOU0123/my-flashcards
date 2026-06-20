import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import json
import os
import re
import threading
import shutil
import time
from collections import Counter
import fitz  # PyMuPDF
from PIL import Image, ImageTk
import requests

# 嘗試載入拖曳套件,若使用者尚未安裝 tkinterdnd2 則自動退回「僅可點選檔案」模式
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

# ==========================================
# 0. 全域設定
# ==========================================
LOCAL_DIR = "local_data"
DATA_JSON = os.path.join(LOCAL_DIR, "questions.json")
IMG_DIR = os.path.join(LOCAL_DIR, "images")
CONFIG_JSON = os.path.join(LOCAL_DIR, "config.json")  # 存放API Key、預設模型等使用者偏好設定

AI_MODEL_OPTIONS = ["claude-opus-4-6", "claude-sonnet-4-6", "claude-opus-4-7-max", "claude-opus-4-8-max"]

FONT_NAME = "Microsoft JhengHei UI"

# 色彩主題
COLOR_BG = "#F4F6FB"
COLOR_CARD = "#FFFFFF"
COLOR_PRIMARY = "#5B6EF5"
COLOR_PRIMARY_DARK = "#4453D1"
COLOR_TEXT = "#26293B"
COLOR_SUBTEXT = "#8A8FA3"
COLOR_BORDER = "#E3E6F0"
COLOR_SUCCESS = "#2E9E5B"
COLOR_SUCCESS_BG = "#E7F7EE"
COLOR_DANGER = "#D6536D"
COLOR_DANGER_BG = "#FBEAEE"
COLOR_WARN = "#E0A030"


# ==========================================
# 1. 核心 PDF 解析邏輯
# ==========================================
def get_pdf_images(pdf_path, output_dir="images"):
    """擷取PDF中每一頁的圖片。
    比起單純抓出每個內嵌圖片物件,這裡額外處理了兩個常見的真實狀況:
    1. 同一張圖在PDF裡常被存成上下緊鄰的好幾塊(例如把一張CT影像切成兩半儲存),
       這裡會把彼此緊鄰、有明顯水平重疊的圖塊自動合併回同一張完整圖片。
    2. PDF內嵌圖片的物件順序,不一定等於它在頁面上「由上到下」的視覺順序,
       這裡改成依照合併後的Y座標由上到下排序,讓後續配對題目時順序才是對的。
    另外也會濾掉細長的雜訊小圖(例如分隔線、底線)以及合併後仍然過小的雜訊。"""
    if not os.path.exists(output_dir): os.makedirs(output_dir)
    doc = fitz.open(pdf_path)
    img_map = {}

    def _vgap_and_xoverlap(a, b):
        if a.y1 <= b.y0: vgap = b.y0 - a.y1
        elif b.y1 <= a.y0: vgap = a.y0 - b.y1
        else: vgap = 0
        x_overlap = max(0, min(a.x1, b.x1) - max(a.x0, b.x0))
        min_w = min(a.width, b.width) or 1
        return vgap, x_overlap / min_w

    for page_num in range(len(doc)):
        page = doc[page_num]
        blocks = page.get_text("blocks")
        image_list = page.get_images(full=True)
        drawings = page.get_drawings()

        # 1. 收集所有圖片的rect,先濾掉退化成一條線的雜訊小圖(分隔線/底線等)
        raw_rects = []
        for img in image_list:
            xref = img[0]
            img_rects = page.get_image_rects(xref)
            if not img_rects: continue
            r = img_rects[0]
            if min(r.width, r.height) < 8:
                continue
            raw_rects.append(r)
        if not raw_rects:
            continue

        # 2. 把彼此緊鄰(同一張圖被切成上下幾塊)的圖片rect合併成一塊
        clusters = list(raw_rects)
        merged = True
        while merged:
            merged = False
            new_clusters, used = [], [False] * len(clusters)
            for i in range(len(clusters)):
                if used[i]: continue
                base = clusters[i]; used[i] = True
                for j in range(i + 1, len(clusters)):
                    if used[j]: continue
                    other = clusters[j]
                    vgap, xov = _vgap_and_xoverlap(base, other)
                    if base.intersects(other) or (vgap <= 14 and xov >= 0.25):
                        base = base | other; used[j] = True; merged = True
                new_clusters.append(base)
            clusters = new_clusters

        # 3. 每個cluster再吸附附近的向量繪圖(箭頭/圈選等),並用文字區塊限制邊界避免跨題
        final_rects = []
        for combined_rect in clusters:
            territory_top, territory_bottom = 0, page.rect.height
            for b in blocks:
                b_rect = fitz.Rect(b[:4])
                if b_rect.y1 < combined_rect.y0 and (combined_rect.y0 - b_rect.y1) < 150:
                    territory_top = max(territory_top, b_rect.y0 - 10)
                if b_rect.y0 > combined_rect.y1 and (b_rect.y0 - combined_rect.y1) < 150:
                    territory_bottom = min(territory_bottom, b_rect.y1 + 10)

            search_buffer = 35
            search_rect = combined_rect + (-search_buffer, -search_buffer, search_buffer, search_buffer)
            for d in drawings:
                d_rect = d["rect"]
                if d_rect.intersects(search_rect) and d_rect.width < 400 and d_rect.height < 400:
                    temp_rect = combined_rect | d_rect
                    if temp_rect.y0 >= territory_top and temp_rect.y1 <= territory_bottom:
                        combined_rect = temp_rect

            final_rect = fitz.Rect(
                max(0, combined_rect.x0 - 5), max(territory_top, combined_rect.y0 - 5),
                min(page.rect.width, combined_rect.x1 + 5), min(territory_bottom, combined_rect.y1 + 5)
            )
            # 4. 過濾合併完後仍然太小的雜訊
            if final_rect.width < 30 or final_rect.height < 30:
                continue
            final_rects.append(final_rect)

        # 5. 依「由上到下」的視覺順序排序,而不是PDF內部嵌入順序
        final_rects.sort(key=lambda r: r.y0)

        paths = []
        for idx, final_rect in enumerate(final_rects):
            pix = page.get_pixmap(clip=final_rect, matrix=fitz.Matrix(2, 2), alpha=False)
            image_filename = os.path.join(output_dir, f"p{page_num+1}_i{idx+1}.png")
            pix.save(image_filename)
            paths.append(image_filename)

        if paths: img_map[page_num + 1] = paths
    return img_map


def _collapse_repeats(s):
    """若字串是同一段文字重複2~4次黏在一起(常見於有浮水印重複印刷的封面),收斂成一份"""
    s = s.strip()
    L = len(s)
    if L == 0:
        return s
    for n in range(4, 1, -1):
        if L % n == 0:
            seg = L // n
            part = s[:seg]
            if part * n == s:
                return part
    return s


def _smart_merge_bare_options(text):
    """把『只有選項字母,沒有其他內容』的孤立行(如單獨一行的"A.")
    跟下一行合併,避免選項值被拆斷;但若下一行本身也是孤立標記、
    或看起來像新題目開頭,就不合併(避免誤吃下一題)"""
    lines = text.split('\n')
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        is_bare = re.match(r'^[A-D]\.\s*$', line)
        if is_bare and i + 1 < len(lines):
            nxt = lines[i + 1]
            looks_new_q = re.match(r'^\d{1,3}\.', nxt) and (len(nxt) > 15 or '？' in nxt or '?' in nxt)
            looks_bare = re.match(r'^[A-D]\.\s*$', nxt)
            if not looks_new_q and not looks_bare:
                out.append(line.rstrip() + nxt)
                i += 2
                continue
        out.append(line)
        i += 1
    return '\n'.join(out)


def extract_exam_data(pdf_path):
    """解析題目 PDF,回傳 (題目列表, metadata, 警告訊息列表)"""
    doc = fitz.open(pdf_path)
    page_texts = [doc[i].get_text() for i in range(len(doc))]
    page_texts = [_smart_merge_bare_options(pt) for pt in page_texts]

    # 過濾頁首/頁尾雜訊(只要「每一頁」都出現的短行才視為雜訊,避免誤刪正常題目內容)
    PAGE_NUM_RE = re.compile(r'^第\s*\d+\s*頁$|^\d{1,4}$|^-\s*\d+\s*-$')
    noise_lines = set()
    if len(page_texts) >= 3:
        counter = Counter()
        for pt in page_texts:
            lines = [l.strip() for l in pt.split('\n') if l.strip()]
            if not lines: continue
            edge = set(lines[:2] + lines[-2:])
            counter.update(edge)
        noise_lines = {l for l, c in counter.items() if c == len(page_texts) and len(l) < 40}

    full_text = ""
    page_offsets = []
    for page_num, pt in enumerate(page_texts):
        kept = [l for l in pt.split('\n') if l.strip() not in noise_lines and not PAGE_NUM_RE.match(l.strip())]
        page_offsets.append((len(full_text), page_num + 1))
        full_text += "\n".join(kept) + "\n"

    # 全角字母/括號轉半角,統一格式
    full_text = full_text.translate(str.maketrans('ＡＢＣＤ（）', 'ABCD()'))

    header_text = page_texts[0]
    title_match = re.search(r'(\d+)\s*(年(?:第[一二三四五]次)?)', header_text)
    year_prefix = _collapse_repeats(title_match.group(1) + title_match.group(2)) if title_match else ""
    subject_match = re.search(r'科目名稱[:：]\s*(.*?)(?=\s*(?:考試時間|題\s*數|$))', header_text, re.DOTALL)
    if subject_match:
        raw_subject = _collapse_repeats(subject_match.group(1).replace('\n', '').strip())
        subject_name = re.sub(r'[（\(](?![一二三四五六七八九十][\)）]).*?[）\)]', '', raw_subject).strip()
    else:
        subject_name = os.path.basename(pdf_path)
    metadata = {"deck_name": f"{year_prefix} {subject_name}".strip()}

    warn_log = []
    matches = list(re.finditer(r'(?:^|\n)(\d{1,3})\.\s*(.*?)(?=\n\d{1,3}\.|$)', full_text, re.DOTALL))

    # Pass1: 把題幹裡藏的「子題編號」造成的假分割合併回上一題(常見於「請依下圖回答下列三題」這種題型)
    raw_matches = []  # [題號字串, 內容區塊, 起始位置]
    seen_ids = set()
    for m in matches:
        q_id = int(m.group(1))
        if q_id > 300:
            continue
        if q_id == 0:
            warn_log.append("⚠️ 偵測到一個「第0題」的假match(通常是選項數值斷行造成的誤判),已自動忽略")
            continue
        block = m.group(2)
        if q_id in seen_ids and raw_matches:
            raw_matches[-1][1] += f"\n{q_id}.{block}"
            warn_log.append(f"⚠️ 題號 {q_id} 重複出現,判定為子題編號或雜訊,已合併進上一題「{raw_matches[-1][0]}」")
            continue
        raw_matches.append([str(q_id), block, m.start()])
        seen_ids.add(q_id)

    # Pass2: 從合併後的區塊解析選項
    questions_dict = {}
    skipped_ids = []
    for q_id, block, start_pos in raw_matches:
        block = re.sub(r'\(([A-D])\)\s*', r'\1. ', block)  # (A)(B)(C)(D) 統一轉成 A. B. C. D.
        if not re.search(r'[A-D]\.', block):
            skipped_ids.append(q_id)
            continue
        content_parts = re.split(r'(?<![A-Za-z0-9])([A-D])\.\s*', block)
        content = content_parts[0].strip()
        # 清掉內容開頭殘留的子題編號(例如合併後題幹開頭多出的"2.")
        m2 = re.match(r'^(\d{1,3})\.\s*(.+)', content, re.DOTALL)
        if m2 and m2.group(1) != q_id:
            content = m2.group(2)

        opts_dict = {}
        for i in range(1, len(content_parts), 2):
            label = content_parts[i]
            val = re.sub(r'\s+', ' ', content_parts[i + 1]).strip()
            if label in opts_dict:
                warn_log.append(f"⚠️ 題號 {q_id} 偵測到重複的選項 {label}(可能混入雜訊文字),保留第一次內容,請人工核對")
                continue
            opts_dict[label] = val

        q_page = 1
        for offset, p_num in page_offsets:
            if start_pos >= offset: q_page = p_num
            else: break

        questions_dict[q_id] = {
            "id": q_id, "text": re.sub(r'\s+', ' ', content).strip(),
            "options": opts_dict, "page": q_page
        }

    if skipped_ids:
        warn_log.append(f"⚠️ 以下題號找不到 A-D 選項格式,已跳過: {', '.join(skipped_ids)}")
    got_ids = {int(v) for v in questions_dict.keys()}
    if got_ids:
        missing = sorted(set(range(1, max(got_ids) + 1)) - got_ids)
        if missing:
            warn_log.append(f"⚠️ 題號出現缺漏: {missing}")

    return sorted(questions_dict.values(), key=lambda x: int(x['id'])), metadata, warn_log


def parse_answer_pdf(ans_pdf_path):
    try:
        doc = fitz.open(ans_pdf_path)
        full_text = ""
        for page in doc: full_text += page.get_text() + "\n"
        full_text = full_text.translate(str.maketrans('ＡＢＣＤ＃', 'ABCD#'))
        full_text = full_text.replace("答案標註#者", "REPLACED_TEXT")

        table_start = re.search(r'題\s*[號序]', full_text)
        search_text = full_text[table_start.start():] if table_start else full_text

        ans_list = []
        blocks = re.findall(r'答\s*案\s*(.*?)(?=題\s*[號序]|備\s*註|$)', search_text, re.DOTALL)
        for b in blocks:
            found = re.findall(r'[A-D#]', b)
            ans_list.extend(found)

        remarks_map = {}
        if "備註" in full_text:
            remarks_text = full_text.split("備註")[-1]
            matches = re.findall(r'第\s*(\d+)\s*題\s*[:：]?(.*?)(?=第\s*\d+\s*題|備\s*註|$)', remarks_text, re.DOTALL)
            for q_num, content in matches:
                remarks_map[str(int(q_num))] = content.strip().strip('，, 。')
        return ans_list, remarks_map
    except Exception:
        return [], {}


def classify_pdf_role(path):
    """自動判斷一份PDF是「題目」還是「答案」。
    優先看檔名關鍵字(最快也最準),檔名看不出來時才打開PDF內文做判斷。"""
    name = os.path.basename(path)
    has_ans_kw = re.search(r'答案|解答|解析|ans(?:wer)?', name, re.IGNORECASE)
    has_q_kw = re.search(r'試題|题目|考古|question', name, re.IGNORECASE)
    if has_ans_kw and not has_q_kw:
        return "answer"
    if has_q_kw and not has_ans_kw:
        return "question"

    # 檔名無法判斷(兩者都有關鍵字、或都沒有),改用內文特徵判斷
    try:
        doc = fitz.open(path)
        text = doc[0].get_text()
        if re.search(r'標準答案|測驗式試題標準答案|答案欄', text):
            return "answer"
        if len(re.findall(r'\n[A-D]\.', text)) >= 3:
            return "question"
    except Exception:
        pass
    return "question"  # 無法判斷時預設為題目,使用者仍可手動點擊切換


# ==========================================
# 2. 共用元件:拖曳/點選匯入框(自動辨識題目／答案)
# ==========================================
class ImportZone(tk.Frame):
    """合併後的匯入框:題目PDF與答案PDF可以一起拖曳進來,
    系統會依檔名(找不到再看內文)自動判斷每個檔案是題目還是答案,
    使用者也可以點擊檔案旁邊的標籤手動切換分類。"""
    def __init__(self, parent, q_var, a_var, on_change=None, **kwargs):
        super().__init__(parent, bg=COLOR_BG, **kwargs)
        self.q_var = q_var
        self.a_var = a_var
        self.on_change = on_change
        self.files = []  # [{"path": str, "role": "question"|"answer"}, ...]

        header = tk.Frame(self, bg=COLOR_BG)
        header.pack(fill="x", pady=(0, 4))
        tk.Label(header, text="匯入 PDF（題目、答案一起拖進來，系統會自動辨識）",
                 font=(FONT_NAME, 10, "bold"), bg=COLOR_BG, fg=COLOR_TEXT).pack(side="left")
        self.clear_link = tk.Label(header, text="清空", font=(FONT_NAME, 9, "underline"),
                                    bg=COLOR_BG, fg=COLOR_SUBTEXT, cursor="hand2")
        self.clear_link.pack(side="right")
        self.clear_link.bind("<Button-1>", lambda e: self.reset())

        self.zone = tk.Frame(self, bg="#FFFFFF", highlightbackground=COLOR_BORDER,
                              highlightthickness=1, bd=0, height=78, cursor="hand2")
        self.zone.pack(fill="x")
        self.zone.pack_propagate(False)

        self._default_hint = "📂  拖曳 PDF 到這裡（題目、答案都可以丟），或點此從資料夾選擇" if DND_AVAILABLE \
            else "📂  點此從資料夾選擇 PDF 檔案（題目、答案都可以一起選）"
        self.hint = tk.Label(self.zone, text=self._default_hint, bg="#FFFFFF",
                              fg=COLOR_SUBTEXT, font=(FONT_NAME, 10))
        self.hint.pack(expand=True)

        for w in (self.zone, self.hint):
            w.bind("<Button-1>", self._choose_files)
            w.bind("<Enter>", lambda e: self._set_hover(True))
            w.bind("<Leave>", lambda e: self._set_hover(False))

        if DND_AVAILABLE:
            self.zone.drop_target_register(DND_FILES)
            self.zone.dnd_bind('<<Drop>>', self._on_drop)
        else:
            tip = tk.Label(self, text="（如需拖曳功能,請先安裝套件: pip install tkinterdnd2）",
                            bg=COLOR_BG, fg=COLOR_WARN, font=(FONT_NAME, 8))
            tip.pack(anchor="w", pady=(2, 0))

        # 檔案清單採用「固定最大高度 + 可滾動」容器,避免檔案一多時無限往下長高,
        # 導致下方的 AI 設定卡片與「開始轉換」按鈕被擠到視窗外面/被蓋住(原本是 pack(fill="x") 無上限)
        self.MAX_LIST_HEIGHT = 260  # 清單最多顯示這個高度,超過就出現滾動條
        list_outer = tk.Frame(self, bg=COLOR_BG)
        list_outer.pack(fill="x", pady=(8, 0))

        self.list_canvas = tk.Canvas(list_outer, bg=COLOR_BG, highlightthickness=0, height=0)
        self.list_scrollbar = ttk.Scrollbar(list_outer, orient="vertical", command=self.list_canvas.yview)
        self.list_canvas.configure(yscrollcommand=self.list_scrollbar.set)
        self.list_canvas.pack(side="left", fill="both", expand=True)

        self.list_frame = tk.Frame(self.list_canvas, bg=COLOR_BG)
        self.list_frame_win = self.list_canvas.create_window((0, 0), window=self.list_frame, anchor="nw")

        # 讓內部清單寬度跟著外框走(維持原本 fill="x" 的視覺效果)
        self.list_canvas.bind("<Configure>",
                               lambda e: self.list_canvas.itemconfig(self.list_frame_win, width=e.width))
        # 滑鼠滾輪支援(Windows/Mac 用 MouseWheel,Linux 用 Button-4/5)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.list_canvas.bind(seq, self._on_mousewheel)

    def _set_hover(self, on):
        self.zone.config(highlightbackground=COLOR_PRIMARY if on else COLOR_BORDER)

    def _choose_files(self, event=None):
        paths = filedialog.askopenfilenames(filetypes=[("PDF files", "*.pdf")])
        if paths:
            self._add_paths(list(paths))

    def _on_drop(self, event):
        paths = self.zone.tk.splitlist(event.data)
        self._add_paths(list(paths))

    def _add_paths(self, paths):
        pdf_paths = [p for p in paths if p.lower().endswith(".pdf")]
        if not pdf_paths:
            return
        existing = {f["path"] for f in self.files}
        for p in pdf_paths:
            if p in existing:
                continue
            self.files.append({"path": p, "role": classify_pdf_role(p)})
        self._refresh()

    def _toggle_role(self, item):
        item["role"] = "answer" if item["role"] == "question" else "question"
        self._refresh()

    def _remove_file(self, item):
        self.files = [f for f in self.files if f is not item]
        self._refresh()

    def _refresh(self):
        for w in self.list_frame.winfo_children():
            w.destroy()

        if not self.files:
            self.hint.config(text=self._default_hint, fg=COLOR_SUBTEXT)
            # 沒有檔案時清單區收起來,不佔空間、也不顯示滾動條
            self.list_canvas.configure(height=0)
            self.list_scrollbar.pack_forget()
        else:
            q_count = sum(1 for f in self.files if f["role"] == "question")
            a_count = sum(1 for f in self.files if f["role"] == "answer")
            self.hint.config(
                text=f"✅ 已加入 {len(self.files)} 個檔案（題目 {q_count}・答案 {a_count}）／可繼續拖曳加入更多",
                fg=COLOR_SUCCESS)

            for item in self.files:
                row = tk.Frame(self.list_frame, bg=COLOR_CARD, highlightbackground=COLOR_BORDER,
                                highlightthickness=1, bd=0)
                row.pack(fill="x", pady=2)

                name = os.path.basename(item["path"])
                if len(name) > 38: name = name[:35] + "…"
                tk.Label(row, text=name, bg=COLOR_CARD, fg=COLOR_TEXT, font=(FONT_NAME, 9),
                         anchor="w").pack(side="left", fill="x", expand=True, padx=10, pady=3)

                is_q = item["role"] == "question"
                pill = tk.Label(row, text=("📘 題目" if is_q else "📗 答案"),
                                 bg=("#E7ECFB" if is_q else COLOR_SUCCESS_BG),
                                 fg=(COLOR_PRIMARY if is_q else COLOR_SUCCESS),
                                 font=(FONT_NAME, 9, "bold"), padx=8, pady=2, cursor="hand2")
                pill.pack(side="left", padx=4)
                pill.bind("<Button-1>", lambda e, it=item: self._toggle_role(it))

                close_btn = tk.Label(row, text="✕", bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                                      font=(FONT_NAME, 9), cursor="hand2", padx=8)
                close_btn.pack(side="right", padx=4)
                close_btn.bind("<Button-1>", lambda e, it=item: self._remove_file(it))

            tip = tk.Label(self.list_frame, text="點擊「📘題目／📗答案」標籤可手動切換分類",
                            bg=COLOR_BG, fg=COLOR_SUBTEXT, font=(FONT_NAME, 8))
            tip.pack(anchor="w", pady=(4, 0))

            # 量出整份清單實際需要的高度,再決定:
            # - 不超過上限 -> 剛好顯示全部、不需要滾動條
            # - 超過上限 -> 高度固定在上限,多出來的部分用滾動條捲動查看
            self.list_frame.update_idletasks()
            content_h = self.list_frame.winfo_reqheight()
            shown_h = min(content_h, self.MAX_LIST_HEIGHT)
            self.list_canvas.configure(height=shown_h, scrollregion=(0, 0, 0, content_h))
            if content_h > self.MAX_LIST_HEIGHT:
                self.list_scrollbar.pack(side="right", fill="y")
            else:
                self.list_scrollbar.pack_forget()
                self.list_canvas.yview_moveto(0)

            # 讓滑鼠停在清單任何一行上滾動滾輪都能生效(而不是只能在空白處滾動)
            self._bind_wheel_recursive(self.list_frame)

        self.q_var.set(";".join(f["path"] for f in self.files if f["role"] == "question"))
        self.a_var.set(";".join(f["path"] for f in self.files if f["role"] == "answer"))
        if self.on_change:
            self.on_change()

    def _bind_wheel_recursive(self, widget):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(seq, self._on_mousewheel)
        for child in widget.winfo_children():
            self._bind_wheel_recursive(child)

    def _on_mousewheel(self, event):
        # 內容沒有超過上限高度時不需要捲動
        if not self.list_scrollbar.winfo_ismapped():
            return
        if event.num == 4:
            delta = -1
        elif event.num == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self.list_canvas.yview_scroll(delta, "units")

    def reset(self):
        self.files = []
        self._refresh()


# ==========================================
# 3. 主視窗介面 (All-in-One GUI)
# ==========================================
class UltimateApp:
    def __init__(self, root):
        self.root = root
        self.root.title("🗂️ 國考刷題")
        self.root.geometry("640x840")
        self.root.minsize(520, 720)
        self.root.configure(bg=COLOR_BG)

        self.data = {"decks": {}, "active": None}
        self.current_idx = 0
        self.flipped = False

        self.ensure_directories()
        self.load_data()
        self.cfg = self.load_config()
        self.setup_style()

        # 建立分頁框架
        self.notebook = ttk.Notebook(self.root, style="App.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=14, pady=14)

        # 分頁 1: 刷題介面
        self.tab_practice = tk.Frame(self.notebook, bg=COLOR_BG)
        self.notebook.add(self.tab_practice, text="  📖 刷題練習  ")
        self.build_practice_ui()

        # 分頁 2: 轉換介面
        self.tab_converter = tk.Frame(self.notebook, bg=COLOR_BG)
        self.notebook.add(self.tab_converter, text="  ⚙️ 題庫轉換工具  ")
        self.build_converter_ui()

        self.update_practice_ui()
        self.setup_shortcuts()

    # --- 視覺主題設定 ---
    def setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("App.TNotebook", background=COLOR_BG, borderwidth=0)
        style.configure("App.TNotebook.Tab", font=(FONT_NAME, 11, "bold"),
                         padding=[16, 10], background=COLOR_BG, foreground=COLOR_SUBTEXT)
        style.map("App.TNotebook.Tab",
                  background=[("selected", COLOR_CARD)],
                  foreground=[("selected", COLOR_PRIMARY)])

        style.configure("Primary.TButton", font=(FONT_NAME, 11, "bold"),
                         padding=10, background=COLOR_PRIMARY, foreground="white", borderwidth=0)
        style.map("Primary.TButton", background=[("active", COLOR_PRIMARY_DARK)])

        style.configure("Nav.TButton", font=(FONT_NAME, 11, "bold"),
                         padding=10, background=COLOR_CARD, foreground=COLOR_TEXT, borderwidth=1)
        style.map("Nav.TButton", background=[("active", "#EEF0FB")])

        style.configure("Danger.TButton", font=(FONT_NAME, 10), padding=8,
                         background=COLOR_DANGER_BG, foreground=COLOR_DANGER, borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#F5D6DD")])

        style.configure("Ghost.TButton", font=(FONT_NAME, 10), padding=8,
                         background=COLOR_CARD, foreground=COLOR_SUBTEXT, borderwidth=1)

        style.configure("App.TCombobox", font=(FONT_NAME, 10), padding=6)

        style.configure("App.Horizontal.TProgressbar", troughcolor=COLOR_BORDER,
                         background=COLOR_PRIMARY, thickness=8, borderwidth=0)
        style.configure("App.Horizontal.TProgressbar", troughcolor=COLOR_BORDER,
                         background=COLOR_PRIMARY, thickness=8, borderwidth=0)

        # 【強制修復下拉選單瀑布問題】：從底層直接限制所有選單展開的最大高度 (顯示 6 行)
        self.root.option_add('*TCombobox*Listbox.height', 6)
        self.root.option_add('*TCombobox*Listbox.font', (FONT_NAME, 11))

    # --- 系統初始化 ---
    def ensure_directories(self):
        os.makedirs(LOCAL_DIR, exist_ok=True)
        os.makedirs(IMG_DIR, exist_ok=True)

    def load_data(self):
        if os.path.exists(DATA_JSON):
            try:
                with open(DATA_JSON, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                pass

    def save_data(self):
        with open(DATA_JSON, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def load_config(self):
        if os.path.exists(CONFIG_JSON):
            try:
                with open(CONFIG_JSON, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_config(self):
        cfg = {"api_key": self.api_key.get().strip(), "ai_model": self.ai_model.get().strip()}
        try:
            with open(CONFIG_JSON, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # --- 分頁 1：刷題介面建構 ---
    def build_practice_ui(self):
        outer = tk.Frame(self.tab_practice, bg=COLOR_BG)
        outer.pack(fill="both", expand=True, padx=4, pady=4)

        top_frame = tk.Frame(outer, bg=COLOR_BG)
        top_frame.pack(fill="x", pady=(0, 10))
        tk.Label(top_frame, text="目前題庫", font=(FONT_NAME, 10, "bold"),
                 bg=COLOR_BG, fg=COLOR_TEXT).pack(side="left")
        self.deck_var = tk.StringVar()
        
        # 【關鍵修復】：加入 height=5 強制限制選單往下掉的高度，最多只顯示 5 個選項
        self.deck_cb = ttk.Combobox(top_frame, textvariable=self.deck_var,
                                     state="readonly", style="App.TCombobox", height=5, width=25)
        self.deck_cb.pack(side="left", fill="x", expand=True, padx=8)
        self.deck_cb.bind("<<ComboboxSelected>>", self.on_deck_change)

        # 卡片區
        self.card_frame = tk.Frame(outer, bg=COLOR_CARD, highlightbackground=COLOR_BORDER,
                                    highlightthickness=1, bd=0)
        self.card_frame.pack(fill="both", expand=True, pady=(0, 10))

        info_frame = tk.Frame(self.card_frame, bg=COLOR_CARD)
        info_frame.pack(fill="x", padx=16, pady=(14, 6))
        self.progress_lbl = tk.Label(info_frame, text="0 / 0", bg=COLOR_CARD,
                                      fg=COLOR_TEXT, font=(FONT_NAME, 11, "bold"))
        self.progress_lbl.pack(side="left")

        jump_box = tk.Frame(info_frame, bg=COLOR_CARD)
        jump_box.pack(side="right")
        tk.Label(jump_box, text="跳至第", bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                 font=(FONT_NAME, 9)).pack(side="left")
        self.jump_entry = tk.Entry(jump_box, width=4, justify="center",
                                    font=(FONT_NAME, 10), relief="solid", bd=1,
                                    highlightthickness=1, highlightbackground=COLOR_BORDER)
        self.jump_entry.pack(side="left", padx=4)
        tk.Label(jump_box, text="題 (按 Enter 跳轉)", bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                 font=(FONT_NAME, 9)).pack(side="left")
        self.jump_entry.bind("<Return>", lambda e: self.jump_to_question())

        self.progress_bar = ttk.Progressbar(self.card_frame, style="App.Horizontal.TProgressbar",
                                             orient="horizontal", mode="determinate")
        self.progress_bar.pack(fill="x", padx=16, pady=(0, 10))

        # 題目內容區:文字與圖片「共用同一個Text元件」,圖片直接內嵌在文字流裡面,
        # 就像在文件裡插入一張圖片一樣,文字絕對不會被圖片蓋住,內容太長就用捲軸滑動查看即可。
        content_outer = tk.Frame(self.card_frame, bg=COLOR_CARD)
        content_outer.pack(fill="both", expand=True, padx=12, pady=(0, 4))

        self.content_text = tk.Text(content_outer, bg=COLOR_CARD, wrap="word",
                                     font=(FONT_NAME, 12), borderwidth=0, highlightthickness=0,
                                     padx=8, pady=8, cursor="arrow")
        self.content_scrollbar = ttk.Scrollbar(content_outer, orient="vertical",
                                                command=self.content_text.yview)
        self.content_text.configure(yscrollcommand=self.content_scrollbar.set)
        self.content_text.pack(side="left", fill="both", expand=True)
        self.content_scrollbar.pack(side="right", fill="y")

        self.content_text.tag_configure("bold", font=(FONT_NAME, 13, "bold"), foreground=COLOR_PRIMARY)
        self.content_text.tag_configure("answer_correct", foreground=COLOR_SUCCESS,
                                         font=(FONT_NAME, 12, "bold"))
        self.content_text.tag_configure("ans_box", foreground=COLOR_SUCCESS,
                                         font=(FONT_NAME, 20, "bold"), justify="center")
        self.content_text.tag_configure("option", font=(FONT_NAME, 12))
        self.content_text.config(state="disabled")
        self._current_photo = None  # 保留目前圖片的參照,避免被Python垃圾回收導致圖片消失

        # 操作按鈕區
        control_frame = tk.Frame(outer, bg=COLOR_BG)
        control_frame.pack(fill="x", pady=(0, 8))
        control_frame.columnconfigure(0, weight=1)
        control_frame.columnconfigure(1, weight=1)
        control_frame.columnconfigure(2, weight=1)
        ttk.Button(control_frame, text="⬅️ 上題 (A/←)", style="Nav.TButton",
                   command=self.prev_card).grid(row=0, column=0, sticky="ew", padx=3)
        ttk.Button(control_frame, text="🔄 解答 (S/↓)", style="Primary.TButton",
                   command=self.flip_card).grid(row=0, column=1, sticky="ew", padx=3)
        ttk.Button(control_frame, text="下題 (D/→) ➡️", style="Nav.TButton",
                   command=self.next_card).grid(row=0, column=2, sticky="ew", padx=3)

        del_frame = tk.Frame(outer, bg=COLOR_BG)
        del_frame.pack(fill="x")
        ttk.Button(del_frame, text="🗑️ 刪除此題庫", style="Ghost.TButton",
                   command=self.delete_current).pack(side="left", fill="x", expand=True, padx=(0, 2))
        
        # 【新增】：整合現有題庫按鈕
        ttk.Button(del_frame, text="🔀 整合現有題庫", style="Ghost.TButton",
                   command=self.merge_existing_decks_window).pack(side="left", fill="x", expand=True, padx=2)
                   
        ttk.Button(del_frame, text="🚨 清空全部", style="Danger.TButton",
                   command=self.clear_all).pack(side="right", fill="x", expand=True, padx=(2, 0))

    # --- 分頁 2：轉換介面建構 ---
    def _on_use_ai_changed(self, *args):
        if self.use_ai.get():
            self.merge_decks.set(True)

    def build_converter_ui(self):
        self.q_path = tk.StringVar()
        self.a_path = tk.StringVar()
        self.use_ai = tk.BooleanVar(value=False)
        self.api_key = tk.StringVar(value=self.cfg.get("api_key", ""))
        saved_model = self.cfg.get("ai_model", "")
        self.ai_model = tk.StringVar(value=saved_model if saved_model in AI_MODEL_OPTIONS else "claude-sonnet-4-6")
        self.ai_scope = tk.StringVar(value="")
        self.merge_decks = tk.BooleanVar(value=False)
        self.custom_deck_name = tk.StringVar(value="")
        # 啟用 AI 篩選時,預設順手把「合併成一份」也打開(使用者仍可自行取消)
        self.use_ai.trace_add("write", self._on_use_ai_changed)

        # 整個分頁包進可滾動容器:讓檔案清單、AI 設定、執行按鈕、處理日誌都維持原本該有的高度
        # (日誌不會再被擠到只剩一條縫),內容若超出視窗高度,改用滾動查看即可
        conv_canvas = tk.Canvas(self.tab_converter, bg=COLOR_BG, highlightthickness=0)
        conv_scrollbar = ttk.Scrollbar(self.tab_converter, orient="vertical", command=conv_canvas.yview)
        conv_canvas.configure(yscrollcommand=conv_scrollbar.set)
        conv_canvas.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        conv_scrollbar.pack(side="right", fill="y", pady=4)

        outer = tk.Frame(conv_canvas, bg=COLOR_BG)
        outer_win = conv_canvas.create_window((0, 0), window=outer, anchor="nw")

        conv_canvas.bind("<Configure>", lambda e: conv_canvas.itemconfig(outer_win, width=e.width))
        outer.bind("<Configure>", lambda e: conv_canvas.configure(scrollregion=conv_canvas.bbox("all")))

        # 1. 匯入框(題目、答案合併,自動辨識)
        self.import_zone = ImportZone(outer, self.q_path, self.a_path)
        self.import_zone.pack(fill="x", pady=(0, 12))

        # 2. 輸出設定(合併成一份題庫 + 自訂名稱)
        out_card = tk.Frame(outer, bg=COLOR_CARD, highlightbackground=COLOR_BORDER,
                             highlightthickness=1, bd=0)
        out_card.pack(fill="x", pady=(0, 12))
        tk.Label(out_card, text="📦 輸出設定", font=(FONT_NAME, 10, "bold"),
                 bg=COLOR_CARD, fg=COLOR_TEXT).pack(anchor="w", padx=14, pady=(12, 6))
        tk.Checkbutton(out_card, text="把所有題目 PDF 合併成同一份題庫", variable=self.merge_decks,
                        font=(FONT_NAME, 10), fg=COLOR_TEXT, bg=COLOR_CARD,
                        activebackground=COLOR_CARD, selectcolor=COLOR_CARD).pack(anchor="w", padx=14)
        tk.Label(out_card, text="（啟用 AI 篩選時會預設勾選此項，仍可自行取消）",
                 font=(FONT_NAME, 8), bg=COLOR_CARD, fg=COLOR_SUBTEXT).pack(anchor="w", padx=14)
        tk.Label(out_card, text="自訂題庫名稱（留空則自動命名）：", bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                 font=(FONT_NAME, 9)).pack(anchor="w", padx=14, pady=(8, 0))
        tk.Entry(out_card, textvariable=self.custom_deck_name, font=(FONT_NAME, 10),
                 relief="solid", bd=1, highlightthickness=0).pack(fill="x", padx=14, pady=(0, 14))

        # 3. AI 設定區塊
        ai_card = tk.Frame(outer, bg=COLOR_CARD, highlightbackground=COLOR_BORDER,
                            highlightthickness=1, bd=0)
        ai_card.pack(fill="x", pady=(0, 12))
        tk.Label(ai_card, text="🧠 AI 篩選設定", font=(FONT_NAME, 10, "bold"),
                 bg=COLOR_CARD, fg=COLOR_TEXT).pack(anchor="w", padx=14, pady=(12, 6))

        chk = tk.Checkbutton(ai_card, text="啟用 AI 語意篩選 (需網路連線)", variable=self.use_ai,
                              font=(FONT_NAME, 10, "bold"), fg=COLOR_DANGER, bg=COLOR_CARD,
                              activebackground=COLOR_CARD, selectcolor=COLOR_CARD)
        chk.pack(anchor="w", padx=14)

        tk.Label(ai_card, text="API Key，輸入後會自動保存)：", bg=COLOR_CARD,
                 fg=COLOR_SUBTEXT, font=(FONT_NAME, 9)).pack(anchor="w", padx=14, pady=(8, 0))
        tk.Entry(ai_card, textvariable=self.api_key, show="*", font=(FONT_NAME, 10),
                 relief="solid", bd=1, highlightthickness=0).pack(fill="x", padx=14)

        tk.Label(ai_card, text="模型選擇：", bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                 font=(FONT_NAME, 9)).pack(anchor="w", padx=14, pady=(8, 0))
        ttk.Combobox(ai_card, textvariable=self.ai_model, state="readonly",
                     values=AI_MODEL_OPTIONS, style="App.TCombobox").pack(fill="x", padx=14)

        tk.Label(ai_card, text="篩選範圍 (AI 將為你剔除不相關的題目)：", bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                 font=(FONT_NAME, 9)).pack(anchor="w", padx=14, pady=(8, 0))
        tk.Entry(ai_card, textvariable=self.ai_scope, font=(FONT_NAME, 10),
                 relief="solid", bd=1, highlightthickness=0).pack(fill="x", padx=14, pady=(0, 14))
        # 【新增】：AI 講義參考輸入區塊
        self.ref_path = tk.StringVar(value="")
        tk.Label(ai_card, text="📚 參考講義 PDF (選填，供 AI 篩選時對照內容)：", bg=COLOR_CARD, fg=COLOR_SUBTEXT,
                 font=(FONT_NAME, 9)).pack(anchor="w", padx=14, pady=(8, 0))
        ref_frame = tk.Frame(ai_card, bg=COLOR_CARD)
        ref_frame.pack(fill="x", padx=14, pady=(0, 14))
        tk.Entry(ref_frame, textvariable=self.ref_path, font=(FONT_NAME, 10),
                 relief="solid", bd=1, highlightthickness=0).pack(side="left", fill="x", expand=True)
        tk.Button(ref_frame, text="選取講義", command=lambda: self.ref_path.set(";".join(filedialog.askopenfilenames(filetypes=[("PDF files", "*.pdf")]))), font=(FONT_NAME, 9)).pack(side="right", padx=(4, 0))

        # 4. 執行按鈕
        self.run_btn = ttk.Button(outer, text="🚀 開始轉換並匯入", style="Primary.TButton",
                                   command=self.start_processing)
        self.run_btn.pack(pady=(2, 12), fill="x")

        tk.Label(outer, text="系統處理日誌", font=(FONT_NAME, 9, "bold"),
                 bg=COLOR_BG, fg=COLOR_SUBTEXT).pack(anchor="w")
        log_wrap = tk.Frame(outer, bg=COLOR_CARD, highlightbackground=COLOR_BORDER, highlightthickness=1)
        log_wrap.pack(fill="both", expand=True, pady=(4, 0))
        self.log_area = scrolledtext.ScrolledText(log_wrap, height=10, state='disabled',
                                                    font=(FONT_NAME, 9), bg=COLOR_CARD,
                                                    borderwidth=0, highlightthickness=0)
        self.log_area.pack(fill="both", expand=True, padx=8, pady=8)
        self.log_area.tag_configure("warn", foreground=COLOR_WARN)
        self.log_area.tag_configure("err", foreground=COLOR_DANGER)
        self.log_area.tag_configure("ok", foreground=COLOR_SUCCESS)

        # 滑鼠滾輪捲動整個分頁;檔案清單與日誌區本身已有各自的捲動邏輯,排除在外避免互搶滾動
        def _on_conv_wheel(event):
            if event.num == 4:
                delta = -1
            elif event.num == 5:
                delta = 1
            else:
                delta = -1 if event.delta > 0 else 1
            conv_canvas.yview_scroll(delta, "units")

        def _bind_wheel_tree(widget, skip_roots):
            if widget in skip_roots:
                return
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(seq, _on_conv_wheel)
            for child in widget.winfo_children():
                _bind_wheel_tree(child, skip_roots)

        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            conv_canvas.bind(seq, _on_conv_wheel)
        _bind_wheel_tree(outer, skip_roots=(self.import_zone, log_wrap))

    # --- 鍵盤快捷鍵 ---
    def setup_shortcuts(self):
        # A/S/D 與左/下/右方向鍵 分別對應 上一題/解答/下一題
        for key in ("a", "A", "<Left>"):
            self.root.bind(key, self._shortcut_prev)
        for key in ("s", "S", "<Down>"):
            self.root.bind(key, self._shortcut_flip)
        for key in ("d", "D", "<Right>"):
            self.root.bind(key, self._shortcut_next)

    def _is_typing_focus(self):
        """若焦點目前在輸入框(跳轉欄、API Key等),不要讓快捷鍵搶走按鍵"""
        w = self.root.focus_get()
        return isinstance(w, (tk.Entry, tk.Text))

    def _on_practice_tab(self):
        return self.notebook.index(self.notebook.select()) == 0

    def _shortcut_prev(self, event=None):
        if self._is_typing_focus() or not self._on_practice_tab(): return
        self.prev_card()

    def _shortcut_flip(self, event=None):
        if self._is_typing_focus() or not self._on_practice_tab(): return
        self.flip_card()

    def _shortcut_next(self, event=None):
        if self._is_typing_focus() or not self._on_practice_tab(): return
        self.next_card()

    # --- 刷題邏輯 ---
    def update_practice_ui(self):
        decks = list(self.data.get("decks", {}).keys())
        decks.sort(reverse=True)
        
        if not decks:
            self.deck_cb['values'] = []
            self.deck_var.set("尚未匯入題庫")
            self.progress_lbl.config(text="0 / 0")
            self.progress_bar['value'] = 0
            self._set_text("目前題庫是空的！\n請點擊上方的「題庫轉換工具」標籤進行匯入。")
            return

        self.deck_cb['values'] = decks
        active = self.data.get("active")
        if active not in decks:
            active = decks[0]
            self.data["active"] = active

        self.deck_var.set(active)
        questions = self.data["decks"][active].get("questions", [])
        if not questions: return

        if self.current_idx >= len(questions) or self.current_idx < 0:
            self.current_idx = 0

        q = questions[self.current_idx]
        self.progress_lbl.config(text=f"{self.current_idx + 1} / {len(questions)}")
        self.progress_bar['maximum'] = len(questions)
        self.progress_bar['value'] = self.current_idx + 1

        self.content_text.config(state="normal")
        self.content_text.delete(1.0, tk.END)
        if not self.flipped:
            self.content_text.insert(tk.END, f"Q{q['id']}\n", "bold")
            self.content_text.insert(tk.END, f"{q['text']}\n\n")
            for key, val in q.get('options', {}).items():
                if val: self.content_text.insert(tk.END, f"({key}) {val}\n", "option")

            # 圖片直接內嵌在文字流的最後面(像文件裡插入圖片一樣),
            # 文字跟圖片是同一個元件裡的內容,絕對不會互相重疊或遮擋。
            # 解答頁刻意不執行這段,所以翻到解答時不會顯示圖片。
            self._current_photo = None
            if "image" in q:
                img_path = os.path.join(LOCAL_DIR, q["image"])
                if os.path.exists(img_path):
                    try:
                        img = Image.open(img_path)
                        img.thumbnail((380, 250), Image.Resampling.LANCZOS)
                        photo = ImageTk.PhotoImage(img)
                        self._current_photo = photo  # 保留參照避免被回收
                        self.content_text.insert(tk.END, "\n")
                        self.content_text.image_create(tk.END, image=photo)
                        self.content_text.insert(tk.END, "\n")
                    except Exception:
                        pass
        else:
            self.content_text.insert(tk.END, "✅ 正確答案\n", "bold")
            ans_val = q.get('answer', 'N/A')
            if len(ans_val) <= 5: self.content_text.insert(tk.END, f"\n{ans_val}\n\n", "ans_box")
            else: self.content_text.insert(tk.END, f"{ans_val}\n\n", "answer_correct")
            self.content_text.insert(tk.END, "──────────\n")
            for key, val in q.get('options', {}).items():
                if key in ans_val and len(ans_val) < 5:
                    self.content_text.insert(tk.END, f"({key}) {val}\n", "answer_correct")
                else:
                    self.content_text.insert(tk.END, f"({key}) {val}\n", "option")
            # 解答頁不顯示圖片,維持純文字方便核對答案
        self.content_text.config(state="disabled")
        self.content_text.yview_moveto(0)  # 每次切換題目都從最上面開始顯示

    def prev_card(self):
        if self.current_idx > 0:
            self.current_idx -= 1; self.flipped = False; self.update_practice_ui()

    def next_card(self):
        qs = self._get_qs()
        if qs and self.current_idx < len(qs) - 1:
            self.current_idx += 1; self.flipped = False; self.update_practice_ui()

    def flip_card(self):
        if self._get_qs():
            self.flipped = not self.flipped; self.update_practice_ui()

    def jump_to_question(self):
        tgt = self.jump_entry.get().strip()
        if tgt.isdigit() and 0 <= int(tgt) - 1 < len(self._get_qs()):
            self.current_idx = int(tgt) - 1; self.flipped = False
            self.jump_entry.delete(0, tk.END); self.update_practice_ui()

    def on_deck_change(self, event):
        sel = self.deck_var.get()
        if sel != self.data.get("active"):
            self.data["active"] = sel; self.current_idx = 0; self.flipped = False
            self.save_data(); self.update_practice_ui()

    def _get_qs(self):
        active = self.data.get("active")
        return self.data["decks"][active].get("questions", []) if active in self.data.get("decks", {}) else []

    def _set_text(self, txt):
        self.content_text.config(state="normal"); self.content_text.delete(1.0, tk.END)
        self.content_text.insert(tk.END, txt); self.content_text.config(state="disabled")

    def delete_current(self):
        act = self.data.get("active")
        if act and messagebox.askyesno("確認", f"刪除題庫「{act}」？"):
            self.data["decks"].pop(act, None)
            rem = list(self.data["decks"].keys())
            self.data["active"] = rem[0] if rem else None
            self.current_idx = 0; self.flipped = False
            self.save_data(); self.update_practice_ui()

    def clear_all(self):
        if messagebox.askyesno("警告", "刪除所有題庫與圖片？此動作無法還原！"):
            self.data = {"decks": {}, "active": None}
            self.current_idx = 0; self.flipped = False
            if os.path.exists(DATA_JSON): os.remove(DATA_JSON)
            if os.path.exists(IMG_DIR):
                shutil.rmtree(IMG_DIR); os.makedirs(IMG_DIR)
            self.update_practice_ui(); messagebox.showinfo("完成", "資料已清空！")

    # --- 轉換與 AI 邏輯 ---
    def log(self, msg, tag=None):
        self.log_area.config(state='normal')
        if tag is None:
            if msg.startswith("⚠️"): tag = "warn"
            elif msg.startswith("❌"): tag = "err"
            elif msg.startswith("✅") or msg.startswith("🎉"): tag = "ok"
        if tag:
            self.log_area.insert(tk.END, msg + "\n", tag)
        else:
            self.log_area.insert(tk.END, msg + "\n")
        self.log_area.see(tk.END)
        self.log_area.config(state='disabled')
        self.root.update()

    def start_processing(self):
        q_pdfs = [p for p in self.q_path.get().split(";") if p.strip()]
        a_pdfs = [p for p in self.a_path.get().split(";") if p.strip()]
        if not q_pdfs: return messagebox.showerror("錯誤", "請選擇題目 PDF！")

        if self.use_ai.get():
            if not self.api_key.get().strip():
                return messagebox.showerror("錯誤", "啟用 AI 篩選需要輸入 API 令牌！")
            if not self.ai_model.get().strip():
                return messagebox.showerror("錯誤", "請選擇模型！")
            if not self.ai_scope.get().strip():
                return messagebox.showerror("錯誤", "啟用 AI 篩選請輸入測驗範圍！")
            self.save_config()  # 記住這次的 API Key 與模型選擇,下次開啟程式不用再輸入一次

        self.run_btn.config(state="disabled")
        self.log_area.config(state='normal'); self.log_area.delete(1.0, tk.END); self.log_area.config(state='disabled')
        
        # 【修改】：抓取講義路徑並一併傳入執行緒
        r_pdfs = [p for p in getattr(self, 'ref_path', tk.StringVar()).get().split(";") if p.strip()]
        threading.Thread(target=self.process_pdfs, args=(q_pdfs, a_pdfs, r_pdfs), daemon=True).start()

    def _short_source_tag(self, base_filename):
        """從檔名擷取簡短的來源標籤(通常是「年份-第幾次」),合併題庫時用來標示每題的出處"""
        nums = re.findall(r'\d+', base_filename)
        if nums:
            return "-".join(nums[:2])
        return base_filename[:6]

    def _unique_deck_name(self, name):
        name = (name or "").strip() or "未命名題庫"
        counter = 1; temp = name
        while temp in self.data["decks"]:
            temp = f"{name} ({counter})"; counter += 1
        return temp

    def _run_ai_filter(self, questions, api_key, scope, model_name, ref_text="", ref_images=None):
        """把題目分批送給 AI 篩選,回傳保留下來的題目清單"""
        if ref_images is None: ref_images = []
        filtered_qs = []
        batch_size = 20
        url = "https://how88.top/v1/chat/completions"
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'}

        for i in range(0, len(questions), batch_size):
            batch = questions[i:i + batch_size]
            self.log(f"⏳ 正在傳送第 {batch[0]['id']} ~ {batch[-1]['id']} 題... (AI 思考中)")

            batch_text = ""
            for q in batch:
                opts = json.dumps(q['options'], ensure_ascii=False)
                batch_text += f"[題號 {q['id']}] {q['text']} | 選項: {opts}\n"

            # 【修改】：動態加入講義內容與組裝 Vision 多模態格式
            ref_prompt = f"以下是你可以對照參考的講義內容：\n{ref_text}\n\n" if ref_text else ""
            prompt = f"""
            你是一位嚴格的專業教授。請根據以下測驗範圍或提示詞【{scope}】，並對照參考內容與圖片，挑選出相關的題目。
            {ref_prompt}
            題目列表：\n{batch_text}
            請「只」回覆符合範圍的題號數字，並用逗號分隔（例如：1,4,12）。
            如果都不符合，請回覆 "None"。絕對不要輸出任何其他解釋或對話。
            """
            
            # 將 Prompt 與講義圖片組裝成標準多模態格式
            user_content = [{"type": "text", "text": prompt}]
            for img in ref_images:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{img['mime']};base64,{img['data']}"}
                })

            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": "你是一個資料過濾助手，請嚴格遵守使用者要求的輸出格式。"},
                    {"role": "user", "content": user_content}
                ],
                "temperature": 0.1
            }
            
            # ... (下面原本的 API 呼叫 try-except 區塊維持不動)

            success = False
            for attempt in range(3):
                try:
                    response = requests.post(url, headers=headers, json=payload, timeout=40)

                    if response.status_code == 200:
                        res_json = response.json()
                        try:
                            result_text = res_json['choices'][0]['message']['content'].strip()
                        except KeyError:
                            result_text = "None"

                        if "none" in result_text.lower():
                            kept_ids = []
                        else:
                            kept_ids = re.findall(r'\d+', result_text)

                        for q in batch:
                            # 題號可能帶有來源前綴(如 "101-1-32"),用結尾數字比對即可
                            tail_num = re.findall(r'\d+', str(q['id']))
                            tail_num = tail_num[-1] if tail_num else str(q['id'])
                            if tail_num in kept_ids or str(q['id']) in kept_ids:
                                filtered_qs.append(q)
                                self.log(f"  ✅ 保留 [第 {q['id']} 題]")
                            else:
                                self.log(f"  ❌ 剔除 [第 {q['id']} 題]")

                        success = True
                        break

                    elif response.status_code == 429:
                        self.log("⚠️ 觸發頻率限制 (429)，冷卻 5 秒...")
                        time.sleep(5)
                    elif response.status_code == 401:
                        self.log("❌ API 令牌無效 (401)！請確認你的 Key 正確。")
                        break
                    else:
                        self.log(f"⚠️ 伺服器回傳錯誤 ({response.status_code}): {response.text[:50]}，等待重試...")
                        time.sleep(5)

                except requests.exceptions.Timeout:
                    self.log("⚠️ 連線超時，準備重試...")
                    time.sleep(2)
                except Exception as e:
                    self.log(f"⚠️ 未知異常: {str(e)[:30]}...")
                    time.sleep(2)

            if not success:
                self.log("❌ 該批次重試 3 次皆失敗，為避免遺漏預設保留。")
                filtered_qs.extend(batch)

            time.sleep(2)

        return filtered_qs

    def process_pdfs(self, q_pdfs, a_pdfs, r_pdfs=None):
        try:
            # 【修改】：提取參考講義文字與圖片
            import base64
            ref_text = ""
            ref_images = []
            if r_pdfs and self.use_ai.get():
                self.log("📚 正在提取參考講義文字與圖片供 AI 判讀...")
                for r_pdf in r_pdfs:
                    try:
                        rdoc = fitz.open(r_pdf)
                        for rpage in rdoc:
                            ref_text += rpage.get_text() + "\n"
                            # 提取頁面中的圖片 (自動過濾掉太小的 icon 或點綴符號)
                            for img_info in rpage.get_images(full=True):
                                if len(ref_images) >= 15: break # 避免 API 圖片數量限制
                                xref = img_info[0]
                                base_img = rdoc.extract_image(xref)
                                img_bytes = base_img["image"]
                                if len(img_bytes) > 15360: # 只抓大於 15KB 的圖，確保是有效圖表
                                    b64 = base64.b64encode(img_bytes).decode('utf-8')
                                    ref_images.append({"mime": f"image/{base_img['ext']}", "data": b64})
                    except Exception as e:
                        self.log(f"⚠️ 無法讀取講義 {os.path.basename(r_pdf)}: {e}")
                
                if len(ref_text) > 100000:
                    ref_text = ref_text[:100000] + "\n...（內容過長，已截斷結尾）"

            def find_best_a(q_name, a_list):
                nums = re.findall(r'\d+', os.path.basename(q_name))
                if not nums: return None
                for a in a_list:
                    if nums[0] in os.path.basename(a): return a
                return None

            # === 第一階段:逐一檔案做文字解析、答案配對、圖片配對(完全不受合併設定影響) ===
            collected = []  # [{"base":..., "raw_qs":[...], "metadata": {...}}, ...]
            for q_pdf in q_pdfs:
                base = os.path.splitext(os.path.basename(q_pdf))[0]
                self.log(f"\n▶ 處理中: {base}")

                matched_a = find_best_a(q_pdf, a_pdfs)
                if matched_a:
                    self.log(f"📋 配對答案: {os.path.basename(matched_a)}")
                    ans_list, remarks = parse_answer_pdf(matched_a)
                else:
                    self.log("⚠️ 找不到答案檔。"); ans_list, remarks = [], {}

                self.log("🖼️ 擷取圖片...")
                deck_img_dir = os.path.join(IMG_DIR, re.sub(r'\W+', '', base))
                os.makedirs(deck_img_dir, exist_ok=True)

                img_map = get_pdf_images(q_pdf, output_dir=deck_img_dir)
                raw_qs, metadata, extract_warnings = extract_exam_data(q_pdf)
                for w in extract_warnings:
                    self.log(w)

                if ans_list and len(ans_list) != len(raw_qs):
                    self.log(f"⚠️ 答案數量({len(ans_list)})與題目數量({len(raw_qs)})不一致,配對可能有錯位,請人工核對！")

                self.log("🧠 組合配對...")
                page_img_cursor = {}  # 同一頁有多張圖片時,依序分配給不同題目,避免全部都搶第一張
                for q in raw_qs:
                    idx = int(q["id"]) - 1
                    q["answer"] = ans_list[idx] if idx < len(ans_list) else "N/A"
                    if q["answer"] == "#" and q["id"] in remarks: q["answer"] = remarks[q["id"]]

                    needs_img = any(k in q["text"] for k in ["圖", "下圖", "附圖"]) or (not q["options"] or all(v == "" for v in q["options"].values()))
                    if needs_img:
                        # 題目文字雖然從 q["page"] 開始,但圖片有時會被排版到「下一頁」才渲染出來,
                        # 所以本頁的圖用完了,就接著找下一頁,而不是直接放棄。
                        for candidate_page in (q["page"], q["page"] + 1):
                            imgs_on_page = img_map.get(candidate_page, [])
                            used = page_img_cursor.get(candidate_page, 0)
                            if used < len(imgs_on_page):
                                q["image"] = os.path.relpath(imgs_on_page[used], LOCAL_DIR)
                                page_img_cursor[candidate_page] = used + 1
                                break

                collected.append({"base": base, "raw_qs": raw_qs, "metadata": metadata})

            # === 第二階段:依「合併成一份」設定決定輸出成幾份題庫,並視需要執行 AI 篩選 ===
            merge = self.merge_decks.get()
            custom_name = self.custom_deck_name.get().strip()
            multi_source = len(collected) > 1
            total = 0

            if merge:
                pool = []
                for item in collected:
                    tag = self._short_source_tag(item["base"])
                    for q in item["raw_qs"]:
                        qc = dict(q)
                        if multi_source:
                            qc["id"] = f"{tag}-{q['id']}"  # 多檔合併時,題號前綴來源避免混淆
                        pool.append(qc)

                if self.use_ai.get():
                    api_key = self.api_key.get().strip()
                    scope = self.ai_scope.get().strip()
                    model_name = self.ai_model.get().strip()
                    self.log(f"\n🤖 開始 AI 智能篩選 (模型：{model_name})，共 {len(pool)} 題待篩選")
                    pool = self._run_ai_filter(pool, api_key, scope, model_name, ref_text, ref_images)
                    default_name = custom_name or f"AI篩選題庫（{scope}）"
                else:
                    default_name = custom_name or " + ".join(
                        it["metadata"].get("deck_name", it["base"]) for it in collected)

                deck_name = self._unique_deck_name(default_name)
                self.data["decks"][deck_name] = {"metadata": {"deck_name": deck_name}, "questions": pool}
                self.data["active"] = deck_name
                total = len(pool)
                self.log(f"\n✅ 已合併 {len(collected)} 份檔案，匯入 {total} 題，題庫名稱：「{deck_name}」")

            else:
                for item in collected:
                    raw_qs, metadata, base = item["raw_qs"], item["metadata"], item["base"]

                    if self.use_ai.get():
                        api_key = self.api_key.get().strip()
                        scope = self.ai_scope.get().strip()
                        model_name = self.ai_model.get().strip()
                        self.log(f"\n🤖 開始 AI 智能篩選 (模型：{model_name})")
                        raw_qs = self._run_ai_filter(raw_qs, api_key, scope, model_name, ref_text, ref_images)
                        metadata["deck_name"] += f" ({scope} 篩選版)"

                    base_name = custom_name if (custom_name and not multi_source) else metadata.get("deck_name", base)
                    if custom_name and multi_source:
                        base_name = f"{custom_name} - {base}"  # 多檔未合併時,自訂名稱當作共同前綴
                    deck_name = self._unique_deck_name(base_name)

                    self.data["decks"][deck_name] = {"metadata": metadata, "questions": raw_qs}
                    self.data["active"] = deck_name
                    total += len(raw_qs)
                    self.log(f"✅ 完成 {base}，匯入 {len(raw_qs)} 題。")

            self.save_data()
            self.log("-" * 40); self.log(f"🎉 批次完成！共匯入 {total} 題。")

            self.root.after(0, self.update_practice_ui)
            self.root.after(0, lambda: self.notebook.select(self.tab_practice))
            self.root.after(0, lambda: messagebox.showinfo("完成", f"匯入成功！\n共產出 {total} 題，已自動切換至刷題頁面。"))

        except Exception as e:
            self.log(f"❌ 發生錯誤: {str(e)}")
            self.root.after(0, lambda e=e: messagebox.showerror("錯誤", f"發生錯誤:\n{str(e)}"))
        finally:
            self.root.after(0, lambda: self.run_btn.config(state="normal"))
            
    # ==========================================
    # 【新增功能】：現有題庫整合 與 參考講義關聯功能
    # ==========================================
    def merge_existing_decks_window(self):
        decks = list(self.data.get("decks", {}).keys())
        if len(decks) < 2:
            return messagebox.showinfo("提示", "目前匯入的題庫不足 2 個，無法進行整合！")
        
        win = tk.Toplevel(self.root)
        win.title("🔀 整合現有題庫")
        win.geometry("400x460")
        win.configure(bg=COLOR_BG)
        win.transient(self.root)
        win.grab_set()
        
        tk.Label(win, text="1. 請選擇要整合的題庫（可多選）：", font=(FONT_NAME, 10, "bold"), bg=COLOR_BG, fg=COLOR_TEXT).pack(anchor="w", padx=15, pady=10)
        
        frame = tk.Frame(win, bg=COLOR_CARD, highlightbackground=COLOR_BORDER, highlightthickness=1)
        frame.pack(fill="both", expand=True, padx=15, pady=5)
        
        scrollbar = ttk.Scrollbar(frame, orient="vertical")
        lb = tk.Listbox(frame, selectmode="extended", font=(FONT_NAME, 11), borderwidth=0, highlightthickness=0, yscrollcommand=scrollbar.set)
        scrollbar.config(command=lb.yview)
        scrollbar.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)
        
        for d in decks: lb.insert(tk.END, d)
            
        tk.Label(win, text="2. 新題庫名稱：", font=(FONT_NAME, 10, "bold"), bg=COLOR_BG, fg=COLOR_TEXT).pack(anchor="w", padx=15, pady=10)
        name_entry = tk.Entry(win, font=(FONT_NAME, 11), relief="solid", bd=1)
        name_entry.pack(fill="x", padx=15, pady=5)
        name_entry.insert(0, "整合歷史題庫")
        
        def do_merge():
            indices = lb.curselection()
            if not indices: return messagebox.showerror("錯誤", "請至少選擇兩個題庫！")
            new_name = name_entry.get().strip()
            if not new_name: return messagebox.showerror("錯誤", "請輸入新題庫名稱！")
                
            combined_qs = []
            for idx in indices:
                d_name = lb.get(idx)
                prefix = re.sub(r'\W+', '', d_name)[:5] # 取字首作為題號區隔防衝突
                for q in self.data["decks"][d_name].get("questions", []):
                    q_copy = dict(q)
                    q_copy["id"] = f"{prefix}-{q['id']}" # 混合題號：例如 113年-1
                    combined_qs.append(q_copy)
            
            final_name = self._unique_deck_name(new_name)
            self.data["decks"][final_name] = {"metadata": {"deck_name": final_name}, "questions": combined_qs}
            self.data["active"] = final_name
            self.current_idx = 0; self.flipped = False
            self.save_data(); self.update_practice_ui()
            win.destroy()
            messagebox.showinfo("完成", f"🎉 成功整合！新題庫共包含 {len(combined_qs)} 題。")
            
        ttk.Button(win, text="🚀 開始整合", style="Primary.TButton", command=do_merge).pack(fill="x", padx=15, pady=15)

if __name__ == "__main__":
    if DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    app = UltimateApp(root)
    root.mainloop()
