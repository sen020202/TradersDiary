import os
import io
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from SmartApi import SmartConnect
import pyotp
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------
# CONFIG
# ---------------------------
CLIENT_ID = "s115376"
PASSWORD = "0011"
API_KEY = "hpU7YEl8"
TOTP_SECRET = "VGDE3STLAS77PCNKTF2EP7E46Q"

INSTRUMENTS_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.csv"
OUTPUT_FILE = f"options_analysis_{datetime.now().strftime('%Y%m%d')}.xlsx"

INDEX_LIST = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

# ---------------------------
# SESSION WITH RETRIES
# ---------------------------
def requests_retry_session(retries=5, backoff_factor=1, status_forcelist=(500,502,504), session=None):
    session = session or requests.Session()
    retry = Retry(total=retries, read=retries, connect=retries,
                  backoff_factor=backoff_factor, status_forcelist=status_forcelist)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

# ---------------------------
# ANGEL LOGIN (pyotp)
# ---------------------------
def angel_login():
    totp = pyotp.TOTP(TOTP_SECRET)
    current_otp = totp.now()
    api = SmartConnect(api_key=API_KEY)
    data = api.generateSession(CLIENT_ID, PASSWORD, current_otp)
    feed_token = data['data']['feedToken']
    return api, feed_token

# ---------------------------
# DOWNLOAD & LOAD INSTRUMENTS
# ---------------------------
def download_instruments():
    resp = requests_retry_session().get(INSTRUMENTS_URL)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    return df

def get_nearest_expiry(df, symbol):
    today = datetime.now().date()
    exp_list = df[(df['name'] == symbol) & (df['exch_seg'] == "NFO")]['expiry'].unique()
    exp_dates = sorted([datetime.strptime(e, "%d%b%Y").date() for e in exp_list])
    for e in exp_dates:
        if e >= today:
            return e.strftime("%d%b%Y").upper()
    return exp_dates[0].strftime("%d%b%Y").upper()

def build_option_token(df, symbol, expiry, strike, opt_type):
    tsym = f"{symbol}{expiry}{int(strike)}{opt_type}"
    row = df[(df['tradingsymbol'] == tsym) & (df['name'] == symbol)]
    if not row.empty:
        return row.iloc[0]['symboltoken'], tsym
    return None, tsym

# ---------------------------
# SPOT PRICE
# ---------------------------
def get_spot_price(api, token, tradingsymbol):
    data = api.ltpData(exchange="NSE", tradingsymbol=tradingsymbol, symboltoken=token)
    return float(data['data']['ltp'])

# ---------------------------
# OPTION LTP
# ---------------------------
def get_option_ltp(api, df, symbol, expiry, strike, opt_type):
    token, tsym = build_option_token(df, symbol, expiry, strike, opt_type)
    if not token:
        return None
    for _ in range(3):
        try:
            data = api.ltpData(exchange="NFO", tradingsymbol=tsym, symboltoken=token)
            return float(data['data']['ltp'])
        except:
            time.sleep(1)
    return None

# ---------------------------
# FIRST 5-MIN CANDLE
# ---------------------------
def get_first_candle(api, df, symbol, expiry, strike, opt_type):
    token, tsym = build_option_token(df, symbol, expiry, strike, opt_type)
    if not token:
        return None, None
    today = datetime.now().date()
    start = datetime.combine(today, datetime.min.time()) + timedelta(hours=9, minutes=15)
    end = start + timedelta(minutes=5)
    params = {
        "exchange": "NFO",
        "symboltoken": token,
        "interval": "FIVE_MINUTE",
        "fromdate": start.strftime("%Y-%m-%d %H:%M"),
        "todate": end.strftime("%Y-%m-%d %H:%M")
    }
    for _ in range(3):
        try:
            candles = api.getCandleData(params)['data']
            if candles:
                o, h, l, c, v = candles[0][1:6]
                return float(h), float(l)
        except:
            time.sleep(1)
    return None, None

# ---------------------------
# ANALYSIS
# ---------------------------
def analyze_symbol(api, df, symbol):
    expiry = get_nearest_expiry(df, symbol)
    print(f"Processing {symbol} (expiry {expiry})...")

    # Spot
    spot_row = df[(df['symbol'] == symbol) & (df['exch_seg'] == "NSE")]
    spot_token = spot_row.iloc[0]['symboltoken']
    spot_tsym = spot_row.iloc[0]['tradingsymbol']
    spot = get_spot_price(api, spot_token, spot_tsym)
    print(f"Spot {symbol}: {spot}")

    atm_strike = round(spot / 50) * 50
    strikes = [atm_strike + i * 50 for i in range(-5, 6)]

    results = []
    for strike in strikes:
        ce_ltp = get_option_ltp(api, df, symbol, expiry, strike, "CE")
        pe_ltp = get_option_ltp(api, df, symbol, expiry, strike, "PE")
        avg_prem = (ce_ltp + pe_ltp)/2 if ce_ltp and pe_ltp else None

        ce_high, ce_low = get_first_candle(api, df, symbol, expiry, strike, "CE")
        pe_high, pe_low = get_first_candle(api, df, symbol, expiry, strike, "PE")
        uds_ce = (ce_low + pe_high)/2 if ce_low and pe_high else None
        uds_pe = (ce_high + pe_low)/2 if ce_high and pe_low else None

        results.append({
            "Strike": strike,
            "CE_LTP": ce_ltp,
            "PE_LTP": pe_ltp,
            "AvgPremium": avg_prem,
            "SPOT-STRIKE_CE": spot - strike,
            "STRIKE-SPOT_PE": strike - spot,
            "UDS_CE": uds_ce,
            "UDS_PE": uds_pe
        })

    return pd.DataFrame(results)

# ---------------------------
# MAIN EXECUTION
# ---------------------------
def main():
    api, feed_token = angel_login()
    df = download_instruments()

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        for symbol in INDEX_LIST:
            df_result = analyze_symbol(api, df, symbol)
            df_result.to_excel(writer, sheet_name=symbol, index=False)

    print(f"Output saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
