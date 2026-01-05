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
import json
import pytz  # For timezone handling

# ---------------------------
# CONFIG
# ---------------------------
CLIENT_ID = "S1125376"
PASSWORD = "0011"
API_KEY = "hpU7YEl8"
# Replace with a valid Base32 TOTP secret (contains only A-Z and 2-7, e.g., JBSWY3DPEHPK3PXP)
TOTP_SECRET = "VGDE3STLAS77PCNKTF2EP7E46Q"  # Placeholder; update with your actual TOTP secret from Angel One

OUTPUT_FILE = f"historical_options_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
DEBUG_FILE = f"historical_options_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

INDEX_LIST = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
INTERVAL = "FIVE_MINUTE"  # Options: ONE_MINUTE, THREE_MINUTE, FIVE_MINUTE, TEN_MINUTE, FIFTEEN_MINUTE, THIRTY_MINUTE, ONE_HOUR, ONE_DAY
FROM_DATE = "2025-10-03 15:25"  # Adjusted to broader range
TO_DATE = "2025-10-06 09:17"    # Up to current date (October 01, 2025)

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
        # Debug: Show available option types for this symbol and expiry
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
        # Debug: Show available strikes
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

# ---------------------------
# HISTORICAL OPTIONS DATA FETCH
# ---------------------------
def get_historical_option_data(api, df, symbol, expiry, strike, opt_type, from_date, to_date, interval):
    """Fetch historical data for an option contract"""
    token, tsym = find_option_token(df, symbol, expiry, strike, opt_type)
    
    if not token:
        return None
    
    params = {
        "exchange": "NFO",
        "symboltoken": token,
        "interval": interval,
        "fromdate": from_date,
        "todate": to_date
    }
    
    print(f"    Fetching historical data for {tsym}: {from_date} to {to_date}, interval: {interval}")
    
    try:
        result = api.getCandleData(params)
        if result and 'status' in result and result['status'] and result['data']:
            data = result['data']
            # Convert to DataFrame: timestamp, open, high, low, close, volume
            df_hist = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            # Convert timestamp to readable date (IST), handling timezone-aware timestamps
            df_hist['date'] = pd.to_datetime(df_hist['timestamp'])
            if df_hist['date'].dt.tz is not None:
                df_hist['date'] = df_hist['date'].dt.tz_convert('Asia/Kolkata').dt.strftime('%Y-%m-%d %H:%M')
            else:
                df_hist['date'] = df_hist['date'].dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata').dt.strftime('%Y-%m-%d %H:%M')
            df_hist['symbol'] = tsym
            df_hist['strike'] = strike
            df_hist['option_type'] = opt_type
            print(f"    ✓ Fetched {len(df_hist)} candles for {tsym}")
            return df_hist
        else:
            print(f"    ✗ No data returned: {result}")
            return None
    except Exception as e:
        print(f"    ✗ Exception fetching historical data: {e}")
        return None

# ---------------------------
# MAIN EXECUTION FOR HISTORICAL OPTIONS DATA
# ---------------------------
def main_historical_options():
    try:
        # Login
        api, feed_token = angel_login()
        
        # Download instruments
        df = download_instruments()
        
        print(f"\n⚠️ IMPORTANT: Check {DEBUG_FILE} to see instrument structure")
        
        # Fetch historical data for options
        print("\n" + "=" * 70)
        print("STARTING HISTORICAL OPTIONS DATA DOWNLOAD")
        print("=" * 70)
        
        data_written = False  # Track if any data is written
        all_data_by_symbol = {}  # Store data for each symbol
        
        for idx, symbol in enumerate(INDEX_LIST, 1):
            print(f"\n[{idx}/{len(INDEX_LIST)}] {symbol}")
            try:
                # Get nearest expiry
                expiry = get_nearest_expiry(df, symbol)
                if not expiry:
                    print(f"⚠️ Cannot proceed without expiry")
                    continue
                
                # Fetch spot price to determine ATM strike
                spot = get_spot_price(api, df, symbol)
                if not spot:
                    print(f"⚠️ Could not fetch spot price")
                    continue
                
                print(f"\n📊 Spot Price: {spot}")
                atm_strike = round(spot / 50) * 50
                strikes = [atm_strike + i * 50 for i in range(-2, 3)]  # ±2 strikes around ATM
                print(f"📍 ATM Strike: {atm_strike}")
                print(f"📊 Strike Range: {strikes[0]} to {strikes[-1]}")
                
                # Fetch data for CE and PE for each strike
                all_data = []
                for strike in strikes:
                    for opt_type in ['CE', 'PE']:
                        df_hist = get_historical_option_data(api, df, symbol, expiry, strike, opt_type, FROM_DATE, TO_DATE, INTERVAL)
                        if df_hist is not None and not df_hist.empty:
                            all_data.append(df_hist)
                        else:
                            print(f"    ⚠️ No data for {symbol} {strike} {opt_type}")
                        time.sleep(0.5)  # Rate limiting
                            
                if all_data:
                    df_combined = pd.concat(all_data, ignore_index=True)
                    all_data_by_symbol[symbol] = df_combined
                    data_written = True
                    print(f"✓ Data collected for {symbol} ({len(df_combined)} rows)")
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
                for symbol, df_combined in all_data_by_symbol.items():
                    df_export = df_combined[['date', 'symbol', 'strike', 'option_type', 'open', 'high', 'low', 'close', 'volume']].copy()
                    df_export.to_excel(writer, sheet_name=symbol, index=False)
                    print(f"✓ Data written to Excel for {symbol}")
            
            print("\n" + "=" * 70)
            print(f"✓✓✓ HISTORICAL OPTIONS DATA DOWNLOAD COMPLETED!")
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
    main_historical_options()
