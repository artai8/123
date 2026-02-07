"""Telegram Login page for tgcf Web UI with environment variable auto-fill."""  
  
import os  
import streamlit as st  
from tgcf.config import CONFIG, write_config  
  
  
def render_telegram_login():  
    """Render the Telegram login configuration page."""  
    st.title("🔑 Telegram Login")  
      
    # Get values from environment variables or config  
    api_id = str(CONFIG.login.API_ID or os.getenv('TGCF_API_ID', ''))  
    api_hash = CONFIG.login.API_HASH or os.getenv('TGCF_API_HASH', '')  
    session_string = CONFIG.login.SESSION_STRING or os.getenv('TGCF_SESSION_STRING', '')  
    bot_token = CONFIG.login.BOT_TOKEN or os.getenv('TGCF_BOT_TOKEN', '')  
    user_type = CONFIG.login.user_type or int(os.getenv('TGCF_USER_TYPE', '0'))  
      
    # Show environment variable status  
    env_status = {  
        'TGCF_API_ID': bool(os.getenv('TGCF_API_ID')),  
        'TGCF_API_HASH': bool(os.getenv('TGCF_API_HASH')),  
        'TGCF_SESSION_STRING': bool(os.getenv('TGCF_SESSION_STRING')),  
        'TGCF_BOT_TOKEN': bool(os.getenv('TGCF_BOT_TOKEN')),  
        'TGCF_USER_TYPE': bool(os.getenv('TGCF_USER_TYPE'))  
    }  
      
    if any(env_status.values()):  
        st.info("🔧 Some values are auto-filled from environment variables")  
        with st.expander("Environment Variable Status"):  
            for var, set in env_status.items():  
                st.write(f"{'✅' if set else '❌'} {var}: {'Set' if set else 'Not set'}")  
      
    # Account type selection  
    account_type = st.radio(  
        "Account Type",  
        options=["User Account", "Bot Account"],  
        index=user_type,  
        help="Choose between User Account (requires phone/session) or Bot Account (requires bot token)"  
    )  
      
    user_type_value = 1 if account_type == "User Account" else 0  
      
    # API Credentials  
    st.subheader("API Credentials")  
    col1, col2 = st.columns(2)  
      
    with col1:  
        new_api_id = st.text_input(  
            "API ID",  
            value=api_id,  
            help="Get this from https://my.telegram.org/apps",  
            key="api_id_input"  
        )  
      
    with col2:  
        new_api_hash = st.text_input(  
            "API Hash",  
            value=api_hash,  
            help="Get this from https://my.telegram.org/apps",  
            key="api_hash_input",  
            type="password"  
        )  
      
    # Authentication based on account type  
    if account_type == "User Account":  
        st.subheader("User Authentication")  
        new_session_string = st.text_area(  
            "Session String",  
            value=session_string,  
            help="Optional: Leave empty to generate new session",  
            key="session_string_input"  
        )  
        new_bot_token = ""  
    else:  
        st.subheader("Bot Authentication")  
        new_bot_token = st.text_input(  
            "Bot Token",  
            value=bot_token,  
            help="Get this from @BotFather",  
            key="bot_token_input"  
        )  
        new_session_string = ""  
      
    # Save button  
    if st.button("💾 Save Configuration", type="primary"):  
        # Update config  
        CONFIG.login.API_ID = int(new_api_id) if new_api_id else 0  
        CONFIG.login.API_HASH = new_api_hash  
        CONFIG.login.user_type = user_type_value  
        CONFIG.login.SESSION_STRING = new_session_string  
        CONFIG.login.BOT_TOKEN = new_bot_token  
          
        # Save to file  
        write_config(CONFIG)  
        st.success("✅ Configuration saved successfully!")  
        st.experimental_rerun()  
      
    # Display current configuration  
    st.subheader("Current Configuration")  
    st.json({  
        "API_ID": CONFIG.login.API_ID,  
        "API_HASH": "***" + CONFIG.login.API_HASH[-4:] if CONFIG.login.API_HASH else "",  
        "user_type": "User Account" if CONFIG.login.user_type == 1 else "Bot Account",  
        "SESSION_STRING": "***" + CONFIG.login.SESSION_STRING[-10:] if CONFIG.login.SESSION_STRING else "",  
        "BOT_TOKEN": "***" + CONFIG.login.BOT_TOKEN[-10:] if CONFIG.login.BOT_TOKEN else ""  
    })  
  
  
def main():  
    """Main function for the Telegram Login page."""  
    render_telegram_login()  
  
  
if __name__ == "__main__":  
    main()
