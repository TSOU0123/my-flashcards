import requests
import csv

def scan_radiographer_exams():
    csv_url = "https://wwwc.moex.gov.tw/main/Exam/wHandExamQandA_CSV.ashx"
    response = requests.get(csv_url)
    response.encoding = 'utf-8-sig'
    
    csv_reader = csv.reader(response.text.splitlines())
    next(csv_reader) # 跳過標題
    
    print("--- 開始偵測所有相關試題名稱 ---")
    seen_exams = set() # 用 set 去除重複
    for row in csv_reader:
        if len(row) < 13: continue
        exam_name = row[2]
        category = row[7]
        subject = row[9]
        
        # 只要跟放射有關，全部抓出來
        if "放射" in category or "放射" in subject or "放射" in exam_name:
            if exam_name not in seen_exams:
                print(f"找到考試: {exam_name}")
                seen_exams.add(exam_name)

if __name__ == "__main__":
    scan_radiographer_exams()