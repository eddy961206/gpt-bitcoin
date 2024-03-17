import random
import sys
import os
from pathlib import Path

# 루트 디렉토리의 경로를 sys.path에 추가
root_directory = Path(__file__).parent.parent
sys.path.append(str(root_directory))


import deepl
from dotenv import load_dotenv
import pyupbit
import pandas as pd
import pandas_ta as ta
import json
from openai import OpenAI
import schedule
import time
from datetime import datetime
import traceback
from slack_bot import send_slack_message, print_and_slack_message

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
UPBIT_ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY")
UPBIT_SECRET_KEY = os.getenv("UPBIT_SECRET_KEY")
GPT_MODEL = os.getenv("GPT_MODEL")

# Setup
client = OpenAI(api_key=OPENAI_API_KEY)
upbit = pyupbit.Upbit(UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY)

# 거래 전후 상태를 저장
pre_trade_status = {}
post_trade_status = {}


# 한화로 환산한 총 보유 자산 (KRW, BTC 포함)
def calculate_total_assets(json_data):
    total_assets = 0
    for entry in json_data:
        if entry["currency"] == "KRW":
            total_assets += float(entry["balance"])
        if entry["currency"] == "BTC":
            total_assets += float(entry["balance"]) * pyupbit.get_current_price(
                "KRW-BTC"
            )
    return total_assets


def format_json_to_slack(json_data):
    formatted_message = "```"
    for entry in json_data:
        formatted_message += f"\nCurrency: {entry['currency']}\nBalance: {entry['balance']}\nLocked: {entry['locked']}\nAvg Buy Price: {entry['avg_buy_price']}\nAvg Buy Price Modified: {entry['avg_buy_price_modified']}\nUnit Currency: {entry['unit_currency']}\n"

    formatted_message += "\n총 보유자산: "
    formatted_message += "{:,.2f}".format(calculate_total_assets(json_data))
    formatted_message += "KRW\n```"
    return formatted_message

# def report_balance():
#     try:
#         balances_json_data = upbit.get_balances()
        
#         formatted_balances_message = format_json_to_slack(balances_json_data)
        
#         print_and_slack_message(formatted_balances_message)
#     except Exception as e:
#         print_and_slack_message(f"잔고 보고 중 오류 발생: {e}")

def report_balance():
    try:
        # 잔고 정보를 가져옵니다.
        krw_balance = upbit.get_balance("KRW")
        btc_balance = upbit.get_balance("BTC")
        btc_avg_buy_price = upbit.get_avg_buy_price("BTC")
        current_btc_price = pyupbit.get_current_price("KRW-BTC")
        
        # 비트코인 평가금액 및 총 보유 자산 계산
        btc_valuation = btc_balance * current_btc_price
        total_assets = krw_balance + btc_valuation
        
        # 평가손익 및 수익률 계산
        valuation_profit_loss = btc_valuation - (btc_avg_buy_price * btc_balance)
        if btc_avg_buy_price > 0:
            return_rate = (valuation_profit_loss / (btc_avg_buy_price * btc_balance)) * 100
        else:
            return_rate = 0
        
        formatted_message = f"```\n원화 보유 자산 : {krw_balance:,.0f} KRW\n코인 보유 자산 : {btc_balance:.5f} BTC\n\n"
        formatted_message += f"코인 매수 평균가 : {btc_avg_buy_price:,.0f} KRW\n코인 평가금액 : {btc_valuation:,.0f} KRW\n\n"
        formatted_message += f"평가손익 : {valuation_profit_loss:,.0f} KRW\n수익률 : {return_rate:.2f}%\n\n"
        formatted_message += f"총 보유 자산 : {total_assets:,.0f} KRW\n```"
        
        print(formatted_message)
    except Exception as e:
        print(f"잔고 보고 중 오류 발생: {e}")


def update_trade_status():
    global pre_trade_status, post_trade_status
    # 거래 전 상태 업데이트
    pre_trade_status = {
        "krw_balance": upbit.get_balance("KRW"),
        "btc_balance": upbit.get_balance("BTC"),
        "avg_buy_price": upbit.get_avg_buy_price("BTC"),
        "btc_valuation": upbit.get_balance("BTC") * pyupbit.get_current_price("KRW-BTC"),
    }

def format_value_change(pre_value, post_value, format_str="{:,.0f}", suffix=""):
    if pre_value == post_value:
        return f"{format_str.format(pre_value)}{suffix} -> 변동 없음"
    else:
        change = post_value - pre_value
        percentage_change = (change / pre_value) * 100 if pre_value else 0
        arrow = ":arrow_up_small:" if change > 0 else ":arrow_down_small:"
        return f"{format_str.format(pre_value)}{suffix} -> {format_str.format(post_value)}{suffix} ({arrow} {abs(percentage_change):.2f}%)"

def compare_trade_status():
    global post_trade_status

    # 잔고 정보를 가져옵니다.
    krw_balance = upbit.get_balance("KRW")
    btc_balance = upbit.get_balance("BTC")
    btc_avg_buy_price = upbit.get_avg_buy_price("BTC")
    current_btc_price = pyupbit.get_current_price("KRW-BTC")

    # 거래 후 상태 업데이트
    post_trade_status = {
        "krw_balance": krw_balance,
        "btc_balance": btc_balance,
        "avg_buy_price": btc_avg_buy_price,
        "btc_valuation": upbit.get_balance("BTC") * current_btc_price,
    }
    
    # 평가손익과 수익률 계산
    # 비트코인 평가금액 및 총 보유 자산 계산
    btc_valuation = btc_balance * current_btc_price
    total_assets = krw_balance + btc_valuation

    # 평가손익 및 수익률 계산
    valuation_profit_loss = btc_valuation - (btc_avg_buy_price * btc_balance)
    if btc_avg_buy_price > 0:
        return_rate = (valuation_profit_loss / (btc_avg_buy_price * btc_balance)) * 100
    else:
        return_rate = 0


    # 메시지 생성
    message = "```\n원화 보유 자산 : " + format_value_change(pre_trade_status["krw_balance"], post_trade_status["krw_balance"], "{:,.0f}", " KRW")
    message += "\n코인 보유 자산 : " + format_value_change(pre_trade_status["btc_balance"], post_trade_status["btc_balance"], "{:.5f}", " BTC")
    message += "\n코인 매수 평균가 : " + format_value_change(pre_trade_status["avg_buy_price"], post_trade_status["avg_buy_price"], "{:,.0f}", " KRW")
    message += "\n코인 평가금액 : " + format_value_change(pre_trade_status["btc_valuation"], post_trade_status["btc_valuation"], "{:,.0f}", " KRW")
    message += f"\n평가손익 : {valuation_profit_loss:,.0f} KRW\n수익률 : {return_rate:.2f}%\n\n"
    message += f"총 보유 자산 : {total_assets:,.0f} KRW\n```"

    print_and_slack_message(message)


def get_current_status():
    orderbook = pyupbit.get_orderbook(ticker="KRW-BTC")
    current_time = orderbook['timestamp']
    btc_balance = 0
    krw_balance = 0
    btc_avg_buy_price = 0
    balances = upbit.get_balances()


    for b in balances:
        if b['currency'] == "BTC":
            btc_balance = b['balance']
            btc_avg_buy_price = b['avg_buy_price']
        if b['currency'] == "KRW":
            krw_balance = b['balance']

    current_status = {
        "current_time": current_time,
        "orderbook": orderbook,
        "btc_balance": btc_balance,
        "krw_balance": krw_balance,
        "btc_avg_buy_price": btc_avg_buy_price,
    }
    return json.dumps(current_status)


def fetch_and_prepare_data():
    global btc_balance
    # Fetch data
    df_daily = pyupbit.get_ohlcv("KRW-BTC", "day", count=30)
    df_hourly = pyupbit.get_ohlcv("KRW-BTC", interval="minute60", count=24)

    # Define a helper function to add indicators
    def add_indicators(df):
        # Moving Averages
        df['SMA_10'] = ta.sma(df['close'], length=10)
        df['EMA_10'] = ta.ema(df['close'], length=10)

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

    return json.dumps(combined_data)


def get_instructions(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            instructions = file.read()
        return instructions
    except FileNotFoundError:
        print_and_slack_message(f"File not found : {file_path}")
    except Exception as e:
        print_and_slack_message(
            f":bug: `An error occurred while reading the file {file_path}`:\n```{e}```"
        )


def analyze_data_with_gpt4(data_json):
    instructions_path = "instructions.md"
    try:
        instructions = get_instructions(instructions_path)
        if not instructions:
            print_and_slack_message(f"{instructions_path}을 찾을 수 없습니다.")
            return None

        current_status = get_current_status()
        response = client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": data_json},
                {"role": "user", "content": current_status}
            ],
            response_format={"type":"json_object"}
        )
        return response.choices[0].message.content
    except Exception as e:
        print_and_slack_message(f":bug: `gpt 분석 중 예상치 못한 오류가 발생했습니다:`\n```{e}```")
        print(traceback.format_exc())
        return None


def execute_buy():
    print("Attempting to buy BTC...")
    try:
        krw = upbit.get_balance("KRW")
        if krw > 5000:
            result = upbit.buy_market_order("KRW-BTC", krw*0.9995)
            print(f"**Buy order successful**\n```{result}```")
    except Exception as e:
        print_and_slack_message(f"**:bug: `Failed to execute buy order**`\n```{e}```")


def execute_sell():
    print("Attempting to sell BTC...")
    try:
        btc = upbit.get_balance("BTC")
        current_price = pyupbit.get_orderbook(ticker="KRW-BTC")['orderbook_units'][0]["ask_price"]
        if current_price*btc > 5000:
            result = upbit.sell_market_order("KRW-BTC", btc)
            print(f"**Sell order successful**\n```{result}```")
    except Exception as e:
        print(f"Failed to execute sell order: {e}")
        print_and_slack_message(f"**:bug: Failed to execute sell order**\n```{e}```")


def make_decision_and_execute():
    print("결정을 내리고 실행 중...")
    data_json = fetch_and_prepare_data()
    # advice = analyze_data_with_gpt4(data_json)

    try:
        # decision = json.loads(advice)
        # decision_text = decision.get('decision')
        # reason_text = decision.get('reason')
        # translated_reason = translate_to_korean(reason_text)
        decision_text = random.choice(["buy", "sell", "hold"])
        translated_reason = random.choice(["코인이 좋습니다.", "코인이 안좋습니다.", "모르겠습니다."])
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 거래 전 상태 업데이트 호출 예
        update_trade_status()

        message_text = ""
        if decision_text == "buy":
            # execute_buy()
            message_text = "**코인을 매수합니다.** :moneybag:"
        elif decision_text == "sell":
            # execute_sell()
            message_text = "**코인을 매도합니다.** :money_with_wings:"
        elif decision_text == "hold":
            message_text = "**코인을 보유합니다.** :eyes:"
        else:
            message_text = "**결정을 내릴 수 없습니다.** :thinking_face:"


        detailed_message = f"[{current_time}]\n{message_text}\n- 이유:\n{translated_reason}"
        print(detailed_message)

        # 거래 후 상태 비교 및 메시지 생성 호출 예
        compare_trade_status()
        
    except Exception as e:
        print(f"Failed to parse the advice as JSON: {e}")


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


if __name__ == "__main__":
    make_decision_and_execute()
    # schedule.every().hour.at(":01").do(make_decision_and_execute)
    schedule.every().day.at("00:01").do(make_decision_and_execute)
    schedule.every().day.at("06:01").do(make_decision_and_execute)
    schedule.every().day.at("12:01").do(make_decision_and_execute)
    schedule.every().day.at("18:01").do(make_decision_and_execute)

    while True:
        schedule.run_pending()
        time.sleep(1)
