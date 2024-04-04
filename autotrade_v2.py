import os
import deepl
from dotenv import load_dotenv
import pyupbit
import pandas as pd
import pandas_ta as ta
import json
from openai import OpenAI
import schedule
import time
import requests
from datetime import datetime
import sqlite3
import traceback
from slack_bot import send_slack_message, print_and_slack_message

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
UPBIT_ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY")
UPBIT_SECRET_KEY = os.getenv("UPBIT_SECRET_KEY")
GPT_MODEL = os.getenv("GPT_MODEL")

HOUR_INTERVAL = 8        # 작동 주기 
MIN_TRADE_AMOUNT = 5000  # 업비트 최소 거래가능 금액(원)
FEE_RATE = 0.0005        # 업비트 수수료 0.05%

# Setup
client = OpenAI(api_key=OPENAI_API_KEY)
upbit = pyupbit.Upbit(UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY)

# 거래 전후 상태를 저장
pre_trade_status = {}
post_trade_status = {}

def initialize_db(db_path='trading_decisions.sqlite'):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME,
                decision TEXT,
                percentage REAL,
                reason TEXT,
                btc_balance REAL,
                krw_balance REAL,
                btc_avg_buy_price REAL
            );
        ''')
        conn.commit()

def save_decision_to_db(decisions, current_status, translated_reason):
    db_path = 'trading_decisions.sqlite'
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
    
        # Parsing current_status from JSON to Python dict
        status_dict = json.loads(current_status)
        
        # Preparing data for insertion
        data_to_insert = (
            decisions.get('decision'),
            decisions.get('percentage', 100),  # Defaulting to 100 if not provided
            translated_reason,
            status_dict.get('btc_balance'),
            status_dict.get('krw_balance'),
            status_dict.get('btc_avg_buy_price')
        )
        
        # Inserting data into the database
        cursor.execute('''
            INSERT INTO decisions (timestamp, decision, percentage, reason, btc_balance, krw_balance, btc_avg_buy_price)
            VALUES (datetime('now', 'localtime'), ?, ?, ?, ?, ?, ?)
        ''', data_to_insert)
    
        conn.commit()

def fetch_last_decisions(db_path='trading_decisions.sqlite', num_decisions=10):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT timestamp, decision, percentage, reason, btc_balance, krw_balance, btc_avg_buy_price FROM decisions
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (num_decisions,))
        decisions = cursor.fetchall()

        if decisions:
            formatted_decisions = []
            for decision in decisions:
                # Converting timestamp to milliseconds since the Unix epoch
                ts = datetime.strptime(decision[0], "%Y-%m-%d %H:%M:%S")
                ts_millis = int(ts.timestamp() * 1000)
                
                formatted_decision = {
                    "timestamp": ts_millis,
                    "decision": decision[1],
                    "percentage": decision[2],
                    "reason": decision[3],
                    "btc_balance": decision[4],
                    "krw_balance": decision[5],
                    "btc_avg_buy_price": decision[6]
                }
                formatted_decisions.append(str(formatted_decision))
            return "\n".join(formatted_decisions)
        else:
            return "No decisions found."

def get_current_status():
    global pre_trade_status

    try:
        # 업비트의 주문장부 정보
        orderbook = pyupbit.get_orderbook(ticker="KRW-BTC")
        current_time = orderbook['timestamp']
        btc_balance = 0
        krw_balance = 0
        btc_avg_buy_price = 0
        
        # 현재 비트코인의 가격 조회
        current_btc_price = pyupbit.get_current_price("KRW-BTC")
        balances = upbit.get_balances()

        for b in balances:
            if b['currency'] == "BTC":
                btc_balance = float(b['balance'])
                btc_avg_buy_price = float(b['avg_buy_price'])
            if b['currency'] == "KRW":
                krw_balance = float(b['balance'])

        # gpt 결정 전 상태 저장 (맨 처음 실행할 때만)
        if pre_trade_status == {}:
            pre_trade_status = {
                "krw_balance": krw_balance,
                "btc_balance": btc_balance,
                "avg_buy_price": btc_avg_buy_price,
                "btc_valuation": btc_balance * current_btc_price,  # 비트코인 평가 금액
                "total_assets": krw_balance + (btc_balance * btc_avg_buy_price),  # 총 자산
            }

        current_status = {
            "current_time": current_time,
            "orderbook": orderbook,
            "btc_balance": btc_balance,
            "krw_balance": krw_balance,
            "btc_avg_buy_price": btc_avg_buy_price,
        }

        return json.dumps(current_status)
    except Exception as e:
        print_and_slack_message(f"현재 상태를 가져오는 중 오류가 발생했습니다: {e}")
        return json.dumps({"error": "현재 상태를 가져오는 중 오류가 발생했습니다."})



def fetch_and_prepare_data():
    global btc_balance
    # Fetch data
    df_daily = pyupbit.get_ohlcv("KRW-BTC", "day", count=30)
    df_hourly = pyupbit.get_ohlcv("KRW-BTC", interval="minute60", count=24)

    # Define a helper function to add indicators
    def add_indicators(df):
        # Moving Averages
        # Calculate and add SMAs for 3, 5, 10, and 20-day periods
        df['SMA_3'] = ta.sma(df['close'], length=3)
        df['SMA_5'] = ta.sma(df['close'], length=5)
        df['SMA_10'] = ta.sma(df['close'], length=10)
        df['SMA_20'] = ta.sma(df['close'], length=20)

        # Calculate and add EMAs for 3, 5, 10, and 20-day periods
        df['EMA_3'] = ta.ema(df['close'], length=3)
        df['EMA_5'] = ta.ema(df['close'], length=5)
        df['EMA_10'] = ta.ema(df['close'], length=10)
        df['EMA_20'] = ta.ema(df['close'], length=20)

        # RSI
        df['RSI_14'] = ta.rsi(df['close'], length=14)

        # Stochastic Oscillator
        stoch = ta.stoch(df['high'], df['low'], df['close'], k=14, d=3, smooth_k=3)
        df = df.join(stoch)

        # MACD
        ema_fast = df['close'].ewm(span=12, adjust=False).mean()
        ema_slow = df['close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = ema_fast - ema_slow
        df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Histogram'] = df['MACD'] - df['Signal_Line']

        # Bollinger Bands
        df['Middle_Band'] = df['close'].rolling(window=20).mean()
        # Calculate the standard deviation of closing prices over the last 20 days
        std_dev = df['close'].rolling(window=20).std()
        # Calculate the upper band (Middle Band + 2 * Standard Deviation)
        df['Upper_Band'] = df['Middle_Band'] + (std_dev * 2)
        # Calculate the lower band (Middle Band - 2 * Standard Deviation)
        df['Lower_Band'] = df['Middle_Band'] - (std_dev * 2)

        return df

    # Add indicators to both dataframes
    df_daily = add_indicators(df_daily)
    df_hourly = add_indicators(df_hourly)

    combined_df = pd.concat([df_daily, df_hourly], keys=['daily', 'hourly'])
    combined_data = combined_df.to_json(orient='split')

    # make combined data as string and print length
    print(len(combined_data))

    return combined_data

def get_news_data():
    ### Get news data from SERPAPI
    url = "https://serpapi.com/search.json?engine=google_news&q=btc&api_key=" + os.getenv("SERPAPI_API_KEY")

    result = "No news data available."

    try:
        response = requests.get(url)
        news_results = response.json()['news_results']

        simplified_news = []
        
        for news_item in news_results:
            # Check if this news item contains 'stories'
            if 'stories' in news_item:
                for story in news_item['stories']:
                    timestamp = int(datetime.strptime(story['date'], '%m/%d/%Y, %H:%M %p, %z %Z').timestamp() * 1000)
                    simplified_news.append((story['title'], story.get('source', {}).get('name', 'Unknown source'), timestamp))
            else:
                # Process news items that are not categorized under stories but check date first
                if news_item.get('date'):
                    timestamp = int(datetime.strptime(news_item['date'], '%m/%d/%Y, %H:%M %p, %z %Z').timestamp() * 1000)
                    simplified_news.append((news_item['title'], news_item.get('source', {}).get('name', 'Unknown source'), timestamp))
                else:
                    simplified_news.append((news_item['title'], news_item.get('source', {}).get('name', 'Unknown source'), 'No timestamp provided'))
        result = str(simplified_news)
    except Exception as e:
        print_and_slack_message(f"Error fetching news data: {e}")

    return result

def fetch_fear_and_greed_index(limit=1, date_format=''):
   """
   최신의 Fear and Greed Index 데이터를 가져오는 함수입니다.

   매개변수:
   - limit (int): 반환할 결과의 개수입니다. 기본값은 1입니다.
   - date_format (str): 날짜 형식입니다. 가능한 값은 'us' (미국), 'cn' (중국), 'kr' (한국), 'world' (세계)입니다. 기본값은 '' (Unix 시간)입니다.

   반환값:
   - dict 또는 str: 지정된 형식의 Fear and Greed Index 데이터입니다. 실패 시 오류 메시지를 반환합니다.
   """
   try:
       base_url = "https://api.alternative.me/fng/"
       params = {
           'limit': limit,
           'format': 'json',
           'date_format': date_format
       }
       response = requests.get(base_url, params=params)
       response.raise_for_status() # 네트워크 오류나 HTTP 응답 상태 코드가 4xx, 5xx인 경우 예외를 발생시킵니다.
       myData = response.json().get('data', [])
       if not myData: # 데이터가 비어있는 경우
           return "Fear and Greed Index API에서 데이터를 반환하지 않았습니다."

       resStr = ""
       for data in myData:
           resStr += str(data)
       return resStr

   except requests.RequestException as e:
       print_and_slack_message(f"Fear and Greed Index를 가져오는 동안 네트워크 관련 오류가 발생했습니다: {e}") 
   except KeyError as e:
       print_and_slack_message(f"Fear and Greed Index 응답에서 예상한 데이터 구조를 찾을 수 없습니다: {e}")
   except Exception as e:
       print_and_slack_message(f"Fear and Greed Index를 가져오는 동안 예기치 않은 오류가 발생했습니다: {e}")

def get_instructions(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            instructions = file.read()
        return instructions
    except FileNotFoundError:
        print("File not found.")
    except Exception as e:
        print("An error occurred while reading the file:", e)

def analyze_data_with_gpt4(news_data, data_json, last_decisions, fear_and_greed, current_status):
    instructions_path = "instructions_v2.md"
    try:
        instructions = get_instructions(instructions_path)
        if not instructions:
            print_and_slack_message(f"{instructions_path}을 찾을 수 없습니다.")
            return None
        
        current_status = get_current_status()
        response = client.chat.completions.create(
            model="gpt-4-turbo-preview",
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": news_data},
                {"role": "user", "content": data_json},
                {"role": "user", "content": last_decisions},
                {"role": "user", "content": fear_and_greed},
                {"role": "user", "content": current_status}
            ],
            response_format={"type":"json_object"}
        )
        advice = response.choices[0].message.content
        print(f"GPT4 분석됨..")
        return advice
    except Exception as e:
        print_and_slack_message(f":bug: `gpt 분석 중 예상치 못한 오류가 발생했습니다:`\n```{e}```")
        print(traceback.format_exc())
        return None

def execute_buy(percentage):
    print(f"보유 원화의 {percentage}% 만큼 매수를 시도합니다...")
    try:
        krw_balance = upbit.get_balance("KRW")
        amount_to_invest = krw_balance * (percentage / 100)
        if amount_to_invest > MIN_TRADE_AMOUNT:
            result = upbit.buy_market_order("KRW-BTC", amount_to_invest * (1 - FEE_RATE))
            if result is None or 'error' in result:  # 매수 주문 실패를 확인
                raise Exception(f"매수 주문 실패: 반환 결과 없음 또는 오류 발생\n{result}")
            print(f"**Buy order successful**\n```{result}```")
        else: 
            raise Exception(f"매수 최소 금액 미달: 필요 : {MIN_TRADE_AMOUNT}, 매수 금액 : {amount_to_invest}")
    except Exception as e:
        print_and_slack_message(f"**:bug: 매수 주문 실패**\n```{e}```")

def execute_sell(percentage):
    print('percentage', percentage)
    print(f"보유 BTC의 {percentage * 100}% 만큼 매도를 시도합니다...")
    try:
        btc_balance = upbit.get_balance("BTC")
        amount_to_sell = btc_balance * (percentage / 100)
        current_price = pyupbit.get_orderbook(ticker="KRW-BTC")['orderbook_units'][0]["ask_price"]
        if current_price * amount_to_sell > MIN_TRADE_AMOUNT:
            result = upbit.sell_market_order("KRW-BTC", amount_to_sell)
            if result is None:
                raise Exception("매도 주문 실패: 반환 결과 없음")
            print(f"**Sell order successful**\n```{result}```")
        else:
            raise Exception(f"매도 최소 금액 미달: 필요 : {MIN_TRADE_AMOUNT}, 현재 : {amount_to_sell * current_price}")
    except Exception as e:
        print_and_slack_message(f"**:bug: 매도 주문 실패**\n```{e}```")

def make_decision_and_execute():
    print("결정을 내리고 실행 중...")
    try:
        news_data = get_news_data()
        data_json = fetch_and_prepare_data()
        last_decisions = fetch_last_decisions()
        fear_and_greed = fetch_fear_and_greed_index(limit=30)
        current_status = get_current_status()
    except Exception as e:
            print_and_slack_message(f"Error: {e}")
    else:
        max_retries = 3
        retry_delay_seconds = 5
        decisions = None
        for attempt in range(max_retries):
            try:
                advice = analyze_data_with_gpt4(news_data, data_json, last_decisions, fear_and_greed, current_status)
                decisions = json.loads(advice)
                break
            except json.JSONDecodeError as e:
                print_and_slack_message(f"JSON 파싱 실패: {e}. {retry_delay_seconds}초 후 재시도 중...")
                time.sleep(retry_delay_seconds)
                print_and_slack_message(f"{attempt + 2}번째 시도 중 / 총 {max_retries}회 시도")
        if not decisions:
            print_and_slack_message(f"최대 재시도 횟수({max_retries})를 초과하여 결정을 내릴 수 없습니다.")
            return
        else:
            try:
                decision   = decisions.get('decision')
                reason     = decisions.get('reason')
                percentage = decisions.get('percentage', 100)

                translated_reason = translate_to_korean(reason)
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                suff_message = ""
                if decision == "buy":
                    execute_buy(percentage)
                    suff_message = f"- :moneybag: {int(percentage * 100)}% 매수! :moneybag:"

                elif decision == "sell":
                    execute_sell(percentage)
                    suff_message = f"- :money_with_wings: {int(percentage * 100)}% 매도! :money_with_wings:"

                elif decision == "hold":
                    suff_message = "- :eyes: 보유합니다 :eyes:"

                else:
                    suff_message = "- :thinking_face: 결정을 내릴 수 없습니다 :thinking_face:"

                detailed_message = f"[{current_time}]\n{suff_message}\n- 이유:\n{translated_reason}"
                print_and_slack_message(detailed_message)

                # gpt 결정 후 상태 비교 및 메시지 전송
                compare_trade_status()
                
                save_decision_to_db(decisions, current_status, translated_reason)
            except Exception as e:
                print_and_slack_message(f"advice를 JSON으로 파싱하는 데 실패했습니다: {e}")


def schedule_tasks(hour_interval):
    for hour in range(0, 24, hour_interval):
        schedule_time = "{:02d}:01".format(hour)    # 01 분마다
        schedule.every().day.at(schedule_time).do(make_decision_and_execute)


#########################################################################################################
############# 기타 함수들 ############# 
#########################################################################################################


def translate_to_korean(text):
    try:
        api_key = os.getenv("DEEPL_API_KEY")
        if not api_key:
            raise ValueError("DeepL API 키가 설정되지 않았습니다.")
        
        translator = deepl.Translator(api_key)
        translated_text = translator.translate_text(text, target_lang="KO").text
        return translated_text
    except ValueError as e:
        print(f"DeepL 환경 변수가 설정되지 않았을 수 있습니다: {e}")
        return translated_text  # API 키 문제 등으로 번역에 실패한 경우 원문 반환
    except deepl.DeepLException as e:
        print(f"DeepL API 호출 중 오류 발생: {e}")
        return translated_text  # DeepL 관련 오류가 발생한 경우 원문 반환
    except Exception as e:
        print(f"번역 중 예기치 않은 오류 발생: {e}")
        return translated_text  # 기타 예외 처리
    

def format_value_change(pre_value, post_value, format_str="{:,.0f}", suffix=""):   # 천 단위 구분자(,), 소수점 X
    if pre_value == post_value:
        return f"{format_str.format(pre_value)}{suffix} -> 변동 없음"
    else:
        change = post_value - pre_value
        percentage_change = (change / pre_value) * 100 if pre_value else 0
        return f"{format_str.format(pre_value)}{suffix} -> {format_str.format(post_value)}{suffix} ({percentage_change:.2f}%)"  # 소수점 아래 두 자리

def compare_trade_status():
    global pre_trade_status
    global post_trade_status

    # 잔고 정보를 가져옵니다.
    krw_balance = upbit.get_balance("KRW")
    btc_balance = upbit.get_balance("BTC")
    btc_avg_buy_price = upbit.get_avg_buy_price("BTC")
    current_btc_price = pyupbit.get_current_price("KRW-BTC")

    # 비트코인 평가금액
    btc_valuation = btc_balance * current_btc_price # 비트코인 평가금액

    # 거래 후 상태 업데이트
    post_trade_status = {
        "krw_balance": krw_balance,
        "btc_balance": btc_balance,
        "avg_buy_price": btc_avg_buy_price,
        "btc_valuation": btc_valuation,
    }
    
    # 거래 후 총 자산 상태 업데이트
    post_trade_status["total_assets"] = krw_balance + btc_valuation  # 총 보유 자산


    # 평가손익 및 수익률 계산
    valuation_profit_loss = btc_valuation - (btc_avg_buy_price * btc_balance)   # 평가손익
    if btc_avg_buy_price > 0:
        return_rate = (valuation_profit_loss / (btc_avg_buy_price * btc_balance)) * 100  # 수익률
    else:
        return_rate = 0

    message = "```\n원화 보유 자산 : " + format_value_change(pre_trade_status["krw_balance"], post_trade_status["krw_balance"], "{:,.0f}", " KRW") # 천 단위 구분자(,), 소수점 X
    message += "\n코인 보유 자산 : " + format_value_change(pre_trade_status["btc_balance"], post_trade_status["btc_balance"], "{:.5f}", " BTC") # 소수점 5자리까지
    message += "\n코인 매수 평균가 : " + format_value_change(pre_trade_status["avg_buy_price"], post_trade_status["avg_buy_price"], "{:,.0f}", " KRW")
    message += "\n코인 평가금액 : " + format_value_change(pre_trade_status["btc_valuation"], post_trade_status["btc_valuation"], "{:,.0f}", " KRW")
    message += f"\n\n평가손익 : {valuation_profit_loss:,.0f} KRW\n수익률 : {return_rate:.2f}%\n\n"
    message += "총 보유 자산 : " + format_value_change(pre_trade_status["total_assets"], post_trade_status["total_assets"], "{:,.0f}", " KRW") + "\n```"

    pre_trade_status = post_trade_status.copy()  # 현재 상태를 과거 상태로 덮어씌우기

    print_and_slack_message(message)



############ 메인 함수 ############
if __name__ == "__main__":
    initialize_db()
    make_decision_and_execute()
    
    schedule_tasks(HOUR_INTERVAL)

    while True:
        schedule.run_pending()
        time.sleep(1)
