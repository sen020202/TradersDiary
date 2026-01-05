import os
import time
import requests
import pandas as pd
from datetime import datetime, timedelta, time as dtime
from SmartApi import SmartConnect
import pyotp
import json
import pytz

# ---------------------------
# CONFIG
# ---------------------------
CLIENT_ID = "S1125376"
PASSWORD = "0011"
API_KEY = "hpU7YEl8"
TOTP_SECRET = "VGDE3STLAS77PCNKTF2EP7E46Q"  # Updated TOTP secret

OUTPUT_FILE = f"options_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
DEBUG_FILE = f"instruments_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

INDEX_LIST = ["NIFTY"]
INTERVAL = "FIVE_MINUTE"

# ---------------------------
# ANGEL LOGIN
# ---------------------------
def get_current_otp():
    """Generate current TOTP using Angel-provided secret"""
    totp = pyotp.TOTP(TOTP_SECRET)
    return totp.now()

def angel_login():
    print("=" * 70)
    print("LOGGING INTO ANGEL ONE...")
    print("=" * 70)
    api = SmartConnect(api_key=API_KEY)

    current_otp = get_current_otp()
    print(f"Generated OTP: {current_otp}")
    
    data = api.generateSession(CLIENT_ID, PASSWORD, current_otp)
    
    if not data or "data" not in data or data["data"] is None:
        print(f"Login Response: {data}")
        raise Exception("Login failed. Check API key, client id, password, or TOTP secret.")

    print("✓ Login Successful!")
    return api, data["data"]["feedToken"]

# ---------------------------
# DOWNLOAD INSTRUMENTS
# ---------------------------
def download_instruments():
    """
    Fetch AngelOne instruments list (JSON) and convert to DataFrame.
    Saves debug info to understand structure.
    """
    url = 'https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json'
    print("\n" + "=" * 70)
    print("DOWNLOADING INSTRUMENTS...")
    print("=" * 70)

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    df = pd.DataFrame(data)
    
    print(f"✓ Downloaded {len(df)} instruments")
    print(f"\nColumn names: {df.columns.tolist()}")
    
    # Save debug info - show sample records for NIFTY
    debug_info = {
        "columns": df.columns.tolist(),
        "total_records": len(df),
        "nifty_nse_sample": df[df['symbol'] == 'NIFTY'].head(2).to_dict('records'),
        "nifty_nfo_sample": df[(df['name'] == 'NIFTY') & (df['exch_seg'] == 'NFO')].head(5).to_dict('records'),
        "nifty_option_sample": df[
            (df['name'] == 'NIFTY') & 
            (df['exch_seg'] == 'NFO') & 
            (df['instrumenttype'] == 'OPTIDX') & 
            (df['symbol'].str.endswith(('CE', 'PE')))
        ].head(5).to_dict('records')
    }
    
    with open(DEBUG_FILE, 'w') as f:
        json.dump(debug_info, f, indent=2, default=str)
    
    print(f"✓ Debug info saved to: {DEBUG_FILE}")
    print(f"\nSample NIFTY NSE record:")
    if debug_info['nifty_nse_sample']:
        for key, val in debug_info['nifty_nse_sample'][0].items():
            print(f"  {key}: {val}")
    
    print(f"\nSample NIFTY Option record:")
    if debug_info['nifty_option_sample']:
        for key, val in debug_info['nifty_option_sample'][0].items():
            print(f"  {key}: {val}")
    
    return df

# ---------------------------
# UTILITY FUNCTIONS
# ---------------------------
def get_previous_trading_day(today):
    """Get the previous trading day, skipping weekends and holidays."""
    holidays_2025 = [
        datetime(2025, 2, 26).date(),
        datetime(2025, 3, 14).date(),
        datetime(2025, 3, 31).date(),
        datetime(2025, 4, 10).date(),
        datetime(2025, 4, 14).date(),
        datetime(2025, 4, 18).date(),
        datetime(2025, 5, 1).date(),
        datetime(2025, 8, 15).date(),
        datetime(2025, 8, 27).date(),
        datetime(2025, 10, 2).date(),
        datetime(2025, 10, 21).date(),
        datetime(2025, 10, 22).date(),
        datetime(2025, 11, 5).date(),
        datetime(2025, 12, 25).date(),
    ]
    prev_day = today - timedelta(days=1)
    while prev_day.weekday() >= 5 or prev_day in holidays_2025:
        prev_day -= timedelta(days=1)
    return prev_day

def get_nearest_expiry(df, symbol):
    """Get nearest expiry date for given symbol"""
    today = datetime.now(pytz.timezone('Asia/Kolkata')).date()
    
    # Filter for NFO options for this symbol
    symbol_df = df[(df['name'] == symbol) & (df['exch_seg'] == "NFO")].copy()
    
    if symbol_df.empty:
        print(f"⚠️ No NFO instruments found for {symbol}")
        return None
    
    # Get unique expiry dates
    exp_list = symbol_df['expiry'].dropna().unique()
    
    if len(exp_list) == 0:
        print(f"⚠️ No expiry dates found for {symbol}")
        return None
    
    print(f"Found {len(exp_list)} unique expiries")
    
    # Parse expiry dates - try multiple formats
    exp_dates = []
    for e in exp_list:
        if pd.isna(e) or e == '':
            continue
        try:
            e_str = str(e).upper().strip()
            # Try different formats
            for fmt in ["%d%b%Y", "%d-%b-%Y", "%Y-%m-%d", "%d%b%y", "%d%B%Y"]:
                try:
                    parsed = datetime.strptime(e_str, fmt).date()
                    exp_dates.append((parsed, e_str))
                    break
                except:
                    continue
        except:
            continue
    
    if not exp_dates:
        print(f"⚠️ Could not parse any expiry dates for {symbol}")
        print(f"Sample expiry values: {list(exp_list[:3])}")
        return None
    
    # Sort by date
    exp_dates.sort(key=lambda x: x[0])
    
    # Format expiry dates outside the f-string
    formatted_expiries = [d[0].strftime("%d%b%Y") for d in exp_dates[:5]]
    print(f"Parsed expiries: {formatted_expiries}")
    
    # Find nearest expiry >= today
    for date, original_str in exp_dates:
        if date >= today:
            print(f"✓ Selected expiry: {original_str} ({date.strftime('%d%b%Y')})")
            return original_str  # Return the original string format from data
    
    # If all expired, return the last one
    print(f"⚠️ All expiries in past, using: {exp_dates[-1][1]}")
    return exp_dates[-1][1]

def find_option_token(df, symbol, expiry, strike, opt_type):
    """
    Find option token using exact matching on the dataframe
    """
    print(f"    Searching: {symbol} {expiry} {strike} {opt_type}")
    
    # Convert strike to float and scale for comparison (assuming strikes are in paise)
    strike_float = float(strike) * 100
    
    # Search with multiple conditions
    result = df[
        (df['name'] == symbol) &
        (df['exch_seg'] == 'NFO') &
        (df['expiry'] == expiry) &
        (df['instrumenttype'] == 'OPTIDX') &
        (df['symbol'].str.endswith(opt_type))
    ].copy()
    
    if result.empty:
        print(f"    ✗ No instruments found for {symbol} {opt_type} with expiry {expiry}")
        available_options = df[
            (df['name'] == symbol) &
            (df['exch_seg'] == 'NFO') &
            (df['expiry'] == expiry) &
            (df['instrumenttype'] == 'OPTIDX')
        ]['symbol'].tolist()
        print(f"    Available options: {available_options[:5]}")
        return None, None
    
    # Now filter by strike
    result['strike_float'] = pd.to_numeric(result['strike'], errors='coerce')
    result = result[abs(result['strike_float'] - strike_float) < 0.01]
    
    if result.empty:
        print(f"    ✗ No match for strike {strike} (scaled: {strike_float})")
        available_strikes = df[
            (df['name'] == symbol) &
            (df['exch_seg'] == 'NFO') &
            (df['expiry'] == expiry) &
            (df['instrumenttype'] == 'OPTIDX') &
            (df['symbol'].str.endswith(opt_type))
        ]['strike'].tolist()
        print(f"    Available strikes: {available_strikes[:5]}")
        return None, None
    
    if len(result) > 1:
        print(f"    ⚠️ Multiple matches found, using first")
    
    row = result.iloc[0]
    token = str(row['token'])
    tsym = row['symbol']
    
    print(f"    ✓ Found: token={token}, symbol={tsym}")
    return token, tsym

def get_spot_price(api, df, symbol):
    """Fetch current spot price"""
    spot_df = df[(df['symbol'] == symbol) & (df['exch_seg'] == 'NSE')]
    
    if spot_df.empty:
        print(f"✗ Spot instrument not found for {symbol} in NSE")
        return None
    
    row = spot_df.iloc[0]
    token = str(row['token'])
    tradingsymbol = row['symbol']
    
    print(f"Spot lookup: symbol={tradingsymbol}, token={token}")
    
    try:
        data = api.ltpData(exchange="NSE", tradingsymbol=tradingsymbol, symboltoken=token)
        
        if data and 'data' in data and 'ltp' in data['data']:
            ltp = float(data['data']['ltp'])
            print(f"✓ Spot LTP: {ltp}")
            return ltp
        else:
            print(f"✗ Invalid spot response: {data}")
            return None
    except Exception as e:
        print(f"✗ Error fetching spot: {e}")
        return None

def get_historical_option_data(api, df, symbol, expiry, strike, opt_type, from_date, to_date, interval):
    """Fetch historical data for an option contract"""
    token, tsym = find_option_token(df, symbol, expiry, strike, opt_type)
    
    if not token:
        return None
    
    from_str = from_date.strftime("%Y-%m-%d %H:%M")
    to_str = to_date.strftime("%Y-%m-%d %H:%M")
    
    params = {
        "exchange": "NFO",
        "symboltoken": token,
        "interval": interval,
        "fromdate": from_str,
        "todate": to_str
    }
    
    print(f"    Fetching historical data for {tsym}: {from_str} to {to_str}, interval: {interval}")
    
    try:
        result = api.getCandleData(params)
        if result and 'status' in result and result['status'] and result['data']:
            data = result['data']
            # Convert to DataFrame: timestamp, open, high, low, close, volume
            df_hist = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            # Convert timestamp to readable date (IST), handling timezone-aware timestamps
            df_hist['date'] = pd.to_datetime(df_hist['timestamp'])
            if df_hist['date'].dt.tz is not None:
                df_hist['date'] = df_hist['date'].dt.tz_convert('Asia/Kolkata')
            else:
                df_hist['date'] = df_hist['date'].dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')
            print(f"    ✓ Fetched {len(df_hist)} candles for {tsym}")
            return df_hist
        else:
            print(f"    ✗ No data returned: {result}")
            return None
    except Exception as e:
        print(f"    ✗ Exception fetching historical data: {e}")
        return None

def get_historical_spot_data(api, df, symbol, from_date, to_date, interval):
    """Fetch historical data for spot index"""
    spot_df = df[(df['symbol'] == symbol) & (df['exch_seg'] == 'NSE')]
    
    if spot_df.empty:
        print(f"✗ Spot instrument not found for {symbol} in NSE")
        return None
    
    token = str(spot_df.iloc[0]['token'])
    
    from_str = from_date.strftime("%Y-%m-%d %H:%M")
    to_str = to_date.strftime("%Y-%m-%d %H:%M")
    
    params = {
        "exchange": "NSE",
        "symboltoken": token,
        "interval": interval,
        "fromdate": from_str,
        "todate": to_str
    }
    
    print(f"    Fetching historical spot data for {symbol}: {from_str} to {to_str}, interval: {interval}")
    
    try:
        result = api.getCandleData(params)
        if result and 'status' in result and result['status'] and result['data']:
            data = result['data']
            df_hist = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_hist['date'] = pd.to_datetime(df_hist['timestamp'])
            if df_hist['date'].dt.tz is not None:
                df_hist['date'] = df_hist['date'].dt.tz_convert('Asia/Kolkata')
            else:
                df_hist['date'] = df_hist['date'].dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')
            print(f"    ✓ Fetched {len(df_hist)} spot candles for {symbol}")
            return df_hist
        else:
            print(f"    ✗ No spot data returned: {result}")
            return None
    except Exception as e:
        print(f"    ✗ Exception fetching spot historical data: {e}")
        return None

# ---------------------------
# ANALYSIS FUNCTION
# ---------------------------
def analyze_symbol(api, df, symbol):
    """Analyze options chain using full previous-day candles for PDO/PDH/PDL per CE & PE"""
    print("\n" + "=" * 70)
    print(f"PROCESSING: {symbol}")
    print("=" * 70)

    # Get expiry
    expiry = get_nearest_expiry(df, symbol)
    if not expiry:
        print(f"⚠️ Cannot proceed without expiry")
        return pd.DataFrame()

    # Fetch current spot price (fallback)
    spot = get_spot_price(api, df, symbol)
    if not spot:
        print(f"⚠️ Could not fetch spot price")
        return pd.DataFrame()

    print(f"\n📊 Current Spot Price: {spot}")

    # Determine dates & time windows (IST)
    tz = pytz.timezone('Asia/Kolkata')
    today = datetime.now(tz).date()
    prev_day = get_previous_trading_day(today)

    # Full previous trading day window (market hours)
    prev_day_start = datetime.combine(prev_day, dtime(9, 15))
    prev_day_end   = datetime.combine(prev_day, dtime(15, 30))

    # Today's first candle (used for spot_open as before)
    today_first_start = datetime.combine(today, dtime(9, 15))
    today_first_end   = datetime.combine(today, dtime(9, 20))

    # Fetch spot opening (first candle open) - fallback to spot LTP if missing
    df_spot_first = get_historical_spot_data(api, df, symbol, today_first_start, today_first_end, INTERVAL)
    spot_open = float(df_spot_first.iloc[0]['open']) if df_spot_first is not None and not df_spot_first.empty else spot
    print(f"📊 Spot Opening Price: {spot_open}")

    # Determine strike range using spot_open
    atm_strike = round(spot_open / 50) * 50
    strikes = [atm_strike + i * 50 for i in range(-5, 6)]

    print(f"📍 ATM Strike: {atm_strike}")
    print(f"📊 Strike Range: {strikes[0]} to {strikes[-1]}\n")

    results = []
    for i, strike in enumerate(strikes, 1):
        print(f"\n[{i}/{len(strikes)}] STRIKE {strike}:")

        # --- Fetch full previous-day candles for CE and PE ---
        print("  Fetching CE full previous-day candles...")
        df_ce_prevday = get_historical_option_data(api, df, symbol, expiry, strike, "CE",
                                                   prev_day_start, prev_day_end, INTERVAL)

        print("  Fetching PE full previous-day candles...")
        df_pe_prevday = get_historical_option_data(api, df, symbol, expiry, strike, "PE",
                                                   prev_day_start, prev_day_end, INTERVAL)

        # --- Also fetch last/close LTP from previous day (we can use the last row close) ---
        ce_ltp = None
        pe_ltp = None
        if df_ce_prevday is not None and not df_ce_prevday.empty:
            # Last recorded close for previous day (last row)
            try:
                ce_ltp = float(df_ce_prevday.iloc[-1]['close'])
            except Exception:
                ce_ltp = None
        else:
            # As fallback try the single-candle last window (existing behavior)
            df_ce_last = get_historical_option_data(api, df, symbol, expiry, strike, "CE",
                                                   prev_day_end - timedelta(minutes=5),
                                                   prev_day_end, INTERVAL)
            if df_ce_last is not None and not df_ce_last.empty:
                ce_ltp = float(df_ce_last.iloc[-1]['close'])

        if df_pe_prevday is not None and not df_pe_prevday.empty:
            try:
                pe_ltp = float(df_pe_prevday.iloc[-1]['close'])
            except Exception:
                pe_ltp = None
        else:
            df_pe_last = get_historical_option_data(api, df, symbol, expiry, strike, "PE",
                                                   prev_day_end - timedelta(minutes=5),
                                                   prev_day_end, INTERVAL)
            if df_pe_last is not None and not df_pe_last.empty:
                pe_ltp = float(df_pe_last.iloc[-1]['close'])

        # --- Compute CE previous-day O/H/L (PDO/PDH/PDL) from the full-day candles ---
        CE_PDO = CE_PDH = CE_PDL = None
        if df_ce_prevday is not None and not df_ce_prevday.empty:
            try:
                # Ensure numeric columns
                df_ce_prevday[['open', 'high', 'low']] = df_ce_prevday[['open', 'high', 'low']].apply(pd.to_numeric, errors='coerce')
                CE_PDO = float(df_ce_prevday.iloc[0]['open'])                         # first candle open (09:15)
                CE_PDH = float(df_ce_prevday['high'].max())                          # day's high
                CE_PDL = float(df_ce_prevday['low'].min())                           # day's low
            except Exception as e:
                print(f"    ✗ Error computing CE previous-day OHL for strike {strike}: {e}")
                CE_PDO = CE_PDH = CE_PDL = None

        # --- Compute PE previous-day O/H/L (PE_PDO/PDH/PDL) ---
        PE_PDO = PE_PDH = PE_PDL = None
        if df_pe_prevday is not None and not df_pe_prevday.empty:
            try:
                df_pe_prevday[['open', 'high', 'low']] = df_pe_prevday[['open', 'high', 'low']].apply(pd.to_numeric, errors='coerce')
                PE_PDO = float(df_pe_prevday.iloc[0]['open'])
                PE_PDH = float(df_pe_prevday['high'].max())
                PE_PDL = float(df_pe_prevday['low'].min())
            except Exception as e:
                print(f"    ✗ Error computing PE previous-day OHL for strike {strike}: {e}")
                PE_PDO = PE_PDH = PE_PDL = None

        # --- For UDS and today's first-candle highs/lows keep previous behavior ---
        print("  Fetching CE first candle (today) for UDS calculation...")
        df_ce_first = get_historical_option_data(api, df, symbol, expiry, strike, "CE", today_first_start, today_first_end, INTERVAL)

        print("  Fetching PE first candle (today) for UDS calculation...")
        df_pe_first = get_historical_option_data(api, df, symbol, expiry, strike, "PE", today_first_start, today_first_end, INTERVAL)

        ce_high = float(df_ce_first.iloc[0]['high']) if df_ce_first is not None and not df_ce_first.empty else None
        ce_low  = float(df_ce_first.iloc[0]['low'])  if df_ce_first is not None and not df_ce_first.empty else None
        pe_high = float(df_pe_first.iloc[0]['high']) if df_pe_first is not None and not df_pe_first.empty else None
        pe_low  = float(df_pe_first.iloc[0]['low'])  if df_pe_first is not None and not df_pe_first.empty else None

        avg_prem = (ce_ltp + pe_ltp) / 2 if ce_ltp is not None and pe_ltp is not None else None
        uds_ce = (ce_low + pe_high) / 2 if ce_low is not None and pe_high is not None else None
        uds_pe = (ce_high + pe_low) / 2 if ce_high is not None and pe_low is not None else None

        # Append result with separate CE_ and PE_ previous-day columns
        results.append({
            "Strike": strike,
            "CE_PDO": CE_PDO,
            "CE_PDH": CE_PDH,
            "CE_PDL": CE_PDL,
            "PE_PDO": PE_PDO,
            "PE_PDH": PE_PDH,
            "PE_PDL": PE_PDL,
            "CE_LTP": ce_ltp,
            "PE_LTP": pe_ltp,
            "AvgPremium": avg_prem,
            "SPOT-STRIKE_CE": spot_open - strike if spot_open is not None else None,
            "STRIKE-SPOT_PE": strike - spot_open if spot_open is not None else None,
            "UDS_CE": uds_ce,
            "UDS_PE": uds_pe
        })

        # Rate limiting
        time.sleep(0.3)

    df_result = pd.DataFrame(results)

    # Print summary
    filled = {
        'CE_LTP': df_result['CE_LTP'].notna().sum(),
        'PE_LTP': df_result['PE_LTP'].notna().sum(),
        'CE_PDO': df_result['CE_PDO'].notna().sum(),
        'PE_PDO': df_result['PE_PDO'].notna().sum()
    }
    print(f"\n✓ Summary for {symbol}:")
    print(f"  CE_LTP: {filled['CE_LTP']}/{len(strikes)}")
    print(f"  PE_LTP: {filled['PE_LTP']}/{len(strikes)}")
    print(f"  CE_PDO: {filled['CE_PDO']}/{len(strikes)}")
    print(f"  PE_PDO: {filled['PE_PDO']}/{len(strikes)}")

    return df_result

# ---------------------------
# MAIN EXECUTION
# ---------------------------
def main():
    try:
        # Login
        api, feed_token = angel_login()
        
        # Download instruments
        df = download_instruments()
        
        print(f"\n⚠️ IMPORTANT: Check {DEBUG_FILE} to see instrument structure")
        
        # Analyze each symbol
        print("\n" + "=" * 70)
        print("STARTING OPTIONS ANALYSIS")
        print("=" * 70)
        
        data_written = False
        all_data_by_symbol = {}
        
        for idx, symbol in enumerate(INDEX_LIST, 1):
            print(f"\n[{idx}/{len(INDEX_LIST)}] {symbol}")
            try:
                df_result = analyze_symbol(api, df, symbol)
                if not df_result.empty:
                    all_data_by_symbol[symbol] = df_result
                    data_written = True
                    print(f"✓ Data collected for {symbol}")
                else:
                    print(f"⚠️ No data collected for {symbol}")
            except Exception as e:
                print(f"✗ Error for {symbol}: {e}")
                import traceback
                traceback.print_exc()
                
            # Pause between symbols
            if idx < len(INDEX_LIST):
                time.sleep(2)
        
        # Save to Excel only if data was collected
        if data_written:
            with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
                for symbol, df_result in all_data_by_symbol.items():
                    df_result.to_excel(writer, sheet_name=symbol, index=False)
                    print(f"✓ Data written to Excel for {symbol}")
            
            print("\n" + "=" * 70)
            print(f"✓✓✓ COMPLETED!")
            print(f"Output: {OUTPUT_FILE}")
            print(f"Debug:  {DEBUG_FILE}")
            print("=" * 70)
        else:
            print("\n" + "=" * 70)
            print("⚠️ No data collected for any symbol. Excel file not created.")
            print(f"Debug:  {DEBUG_FILE}")
            print("=" * 70)
        
    except Exception as e:
        print(f"\n✗ Fatal error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()