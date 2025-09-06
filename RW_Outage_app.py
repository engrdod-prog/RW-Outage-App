import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import os
import sys
from typing import Optional, Dict, Any
import warnings
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
warnings.filterwarnings('ignore')

# Check for required dependencies
try:
    import openpyxl
except ImportError:
    st.error("""
    **Missing Dependency**: `openpyxl` is required to run this app.
    
    Please install it by running:
    ```
    pip install openpyxl
    ```
    """)
    st.stop()

try:
    import plotly
except ImportError:
    st.error("""
    **Missing Dependency**: `plotly` is required for enhanced charts.
    
    Please install it by running:
    ```
    pip install plotly
    ```
    """)
    st.stop()

# ----------------------------
# Config
# ----------------------------
EXCEL_FILE = "RW_Outage.xlsx"
SHEET_NAME = "Downtime Log"
SUMMARY_SHEET = "Summary"
BROADCAST_HOURS_PER_DAY = 17.5  # 4:30 AM to 10:00 PM

# ----------------------------
# Validation functions
# ----------------------------

def validate_time_input(start_time, end_time, date) -> tuple[bool, str]:
    """Validate that end time is after start time and within broadcast hours."""
    if end_time <= start_time:
        return False, "End time must be after start time"
    
    # Check if within broadcast hours (4:30 AM to 10:00 PM)
    start_minutes = start_time.hour * 60 + start_time.minute
    end_minutes = end_time.hour * 60 + end_time.minute
    broadcast_start = 4 * 60 + 30  # 4:30 AM
    broadcast_end = 22 * 60  # 10:00 PM
    
    if start_minutes < broadcast_start or end_minutes > broadcast_end:
        return False, "Times must be within broadcast hours (4:30 AM - 10:00 PM)"
    
    return True, ""

def validate_required_fields(date, start_time, end_time, failure_type) -> tuple[bool, str]:
    """Validate that all required fields are filled."""
    if not date:
        return False, "Date is required"
    if not start_time:
        return False, "Start time is required"
    if not end_time:
        return False, "End time is required"
    if not failure_type or failure_type.strip() == "":
        return False, "Failure type is required"
    return True, ""

def check_duplicate_entry(df, date, start_time, end_time) -> bool:
    """Check if an entry with the same date and time already exists."""
    if df.empty:
        return False
    
    df_copy = df.copy()
    df_copy['Date'] = pd.to_datetime(df_copy['Date'])
    target_date = pd.to_datetime(date)
    
    same_date_entries = df_copy[df_copy['Date'].dt.date == target_date.date()]
    
    for _, row in same_date_entries.iterrows():
        existing_start = pd.to_datetime(str(row['Start Time'])).time()
        existing_end = pd.to_datetime(str(row['End Time'])).time()
        
        # Check for overlap
        if (start_time < existing_end and end_time > existing_start):
            return True
    return False

# ----------------------------
# Helper functions
# ----------------------------

@st.cache_data(ttl=600)  # Cache for 10 minutes
def load_data():
    """Load data from Excel file with proper error handling."""
    if os.path.exists(EXCEL_FILE):
        try:
            df = pd.read_excel(EXCEL_FILE, sheet_name=SHEET_NAME)
            # Ensure proper data types
            df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
            # Sort by date in descending order (newest first)
            df = df.sort_values('Date', ascending=False).reset_index(drop=True)
            return df
        except FileNotFoundError:
            st.error(f"Excel file '{EXCEL_FILE}' not found.")
            return create_empty_dataframe()
        except PermissionError:
            st.error(f"Permission denied. Please close '{EXCEL_FILE}' if it's open in another program.")
            return create_empty_dataframe()
        except Exception as e:
            st.error(f"Error loading data: {str(e)}")
            return create_empty_dataframe()
    else:
        return create_empty_dataframe()

def create_empty_dataframe():
    """Create an empty dataframe with proper column structure."""
    return pd.DataFrame(columns=[
        "Date", "Start Time", "End Time",
        "Downtime (minutes)", "Downtime (hh:mm)",
        "Failure Type", "Remarks"
    ])

def save_data(df):
    """Save data to Excel with comprehensive summary calculations."""
    try:
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl", mode="w") as writer:
            df.to_excel(writer, sheet_name=SHEET_NAME, index=False)

            if not df.empty:
                # Build comprehensive summary
                summary_data = build_comprehensive_summary(df)
                
                # Save monthly summary
                summary_data['monthly'].to_excel(writer, sheet_name="Monthly_Summary", index=False)
                
                # Save yearly summary
                summary_data['yearly'].to_excel(writer, sheet_name="Yearly_Summary", index=False)
                
                # Save detailed summary (backward compatibility)
                summary_data['monthly'].to_excel(writer, sheet_name=SUMMARY_SHEET, index=False)
                
        # Clear cache after saving
        load_data.clear()
        return True
    except PermissionError:
        st.error(f"Permission denied. Please close '{EXCEL_FILE}' if it's open in another program.")
        return False
    except Exception as e:
        st.error(f"Error saving data: {str(e)}")
        return False

@st.cache_data(ttl=300)  # Cache for 5 minutes
def build_comprehensive_summary(df):
    """Build comprehensive monthly and yearly availability summaries."""
    if df.empty:
        return {'monthly': pd.DataFrame(), 'yearly': pd.DataFrame()}
    
    df_copy = df.copy()
    df_copy["Date"] = pd.to_datetime(df_copy["Date"], errors="coerce")
    df_copy = df_copy.dropna(subset=['Date'])
    
    if df_copy.empty:
        return {'monthly': pd.DataFrame(), 'yearly': pd.DataFrame()}
    
    df_copy["Month"] = df_copy["Date"].dt.strftime("%B")
    df_copy["Year"] = df_copy["Date"].dt.year
    df_copy["Month_Num"] = df_copy["Date"].dt.month
    
    # Monthly Summary
    monthly_summary = df_copy.groupby(["Year", "Month", "Month_Num"]).agg(
        Total_Downtime_Minutes=("Downtime (minutes)", "sum"),
        Failure_Count=("Failure Type", "count"),
        Avg_Downtime_Per_Failure=("Downtime (minutes)", "mean")
    ).reset_index()
    
    # Calculate monthly metrics
    monthly_summary["Days_in_Month"] = monthly_summary.apply(
        lambda row: pd.Period(f"{row['Year']}-{row['Month_Num']:02d}").days_in_month,
        axis=1
    )
    monthly_summary["Total_Broadcast_Minutes"] = monthly_summary["Days_in_Month"] * BROADCAST_HOURS_PER_DAY * 60
    monthly_summary["Availability_%"] = ((monthly_summary["Total_Broadcast_Minutes"] - monthly_summary["Total_Downtime_Minutes"]) /
                                        monthly_summary["Total_Broadcast_Minutes"]) * 100
    
    # Sort by year and month
    monthly_summary = monthly_summary.sort_values(["Year", "Month_Num"]).drop("Month_Num", axis=1)
    
    # Yearly Summary
    yearly_summary = df_copy.groupby(["Year"]).agg(
        Total_Downtime_Minutes=("Downtime (minutes)", "sum"),
        Failure_Count=("Failure Type", "count"),
        Avg_Downtime_Per_Failure=("Downtime (minutes)", "mean"),
        Max_Downtime=("Downtime (minutes)", "max"),
        Min_Downtime=("Downtime (minutes)", "min")
    ).reset_index()
    
    # Calculate yearly metrics
    yearly_summary["Days_in_Year"] = yearly_summary["Year"].apply(
        lambda year: 366 if pd.Period(f"{year}").is_leap_year else 365
    )
    yearly_summary["Total_Broadcast_Minutes"] = yearly_summary["Days_in_Year"] * BROADCAST_HOURS_PER_DAY * 60
    yearly_summary["Availability_%"] = ((yearly_summary["Total_Broadcast_Minutes"] - yearly_summary["Total_Downtime_Minutes"]) /
                                       yearly_summary["Total_Broadcast_Minutes"]) * 100
    yearly_summary["Max_Downtime_hours"] = yearly_summary["Max_Downtime"] / 60
    yearly_summary["Min_Downtime_hours"] = yearly_summary["Min_Downtime"] / 60
    
    # Calculate Year-to-Date (YTD) availability for current year
    current_year = datetime.now().year
    current_date = datetime.now().date()
    
    # Get all outages from January 1st of current year to today
    ytd_outages = df_copy[
        (df_copy['Date'].dt.year == current_year) & 
        (df_copy['Date'].dt.date <= current_date)
    ]
    
    ytd_summary = {}
    if not ytd_outages.empty:
        # Calculate days from Jan 1 to current date
        jan_1 = datetime(current_year, 1, 1).date()
        days_elapsed = (current_date - jan_1).days + 1
        
        # Calculate total broadcast minutes for YTD period
        total_ytd_broadcast_minutes = days_elapsed * BROADCAST_HOURS_PER_DAY * 60
        
        # Calculate total downtime for YTD period
        total_ytd_downtime_minutes = ytd_outages['Downtime (minutes)'].sum()
        
        # Calculate YTD availability
        ytd_availability = ((total_ytd_broadcast_minutes - total_ytd_downtime_minutes) / 
                           total_ytd_broadcast_minutes) * 100
        
        ytd_summary = {
            'current_year': current_year,
            'days_elapsed': days_elapsed,
            'total_broadcast_minutes': total_ytd_broadcast_minutes,
            'total_downtime_minutes': total_ytd_downtime_minutes,
            'ytd_availability_%': ytd_availability,
            'failure_count': len(ytd_outages),
            'avg_downtime_per_failure': ytd_outages['Downtime (minutes)'].mean() if len(ytd_outages) > 0 else 0
        }
    
    return {'monthly': monthly_summary, 'yearly': yearly_summary, 'ytd': ytd_summary}

# ----------------------------
# Chart Helper Functions
# ----------------------------

def create_availability_chart(monthly_data):
    """Create an interactive availability trend chart."""
    if monthly_data.empty:
        return None
    
    fig = go.Figure()
    
    # Add availability line
    fig.add_trace(go.Scatter(
        x=monthly_data['Month'],
        y=monthly_data['Availability_%'],
        mode='lines+markers',
        name='Availability %',
        line=dict(color='#1f77b4', width=3),
        marker=dict(size=8),
        hovertemplate='<b>%{x}</b><br>Availability: %{y:.2f}%<extra></extra>'
    ))
    
    # Add target line (99%)
    fig.add_hline(
        y=99, 
        line_dash="dash", 
        line_color="red",
        annotation_text="Target: 99%",
        annotation_position="top right"
    )
    
    fig.update_layout(
        title="Monthly Availability Trend",
        xaxis_title="Month",
        yaxis_title="Availability (%)",
        hovermode='x unified',
        template='plotly_white',
        height=400,
        showlegend=True
    )
    
    return fig

def create_failure_analysis_chart(failure_data):
    """Create an interactive failure type analysis chart."""
    if failure_data.empty:
        return None
    
    fig = go.Figure()
    
    fig.add_trace(go.Bar(
        x=failure_data.index,
        y=failure_data['Count'],
        name='Failure Count',
        marker_color='#ff7f0e',
        hovertemplate='<b>%{x}</b><br>Count: %{y}<extra></extra>'
    ))
    
    fig.update_layout(
        title="Failure Type Distribution",
        xaxis_title="Failure Type",
        yaxis_title="Count",
        template='plotly_white',
        height=400,
        showlegend=False
    )
    
    return fig

def create_downtime_trend_chart(daily_data):
    """Create an interactive daily downtime trend chart."""
    if daily_data.empty:
        return None
    
    fig = go.Figure()
    
    fig.add_trace(go.Scatter(
        x=daily_data.index,
        y=daily_data['Daily_Downtime_Hours'],
        mode='lines+markers',
        name='Daily Downtime (Hours)',
        line=dict(color='#d62728', width=2),
        marker=dict(size=6),
        hovertemplate='<b>%{x}</b><br>Downtime: %{y:.1f}h<extra></extra>'
    ))
    
    fig.update_layout(
        title="Daily Downtime Trend",
        xaxis_title="Date",
        yaxis_title="Downtime (Hours)",
        hovermode='x unified',
        template='plotly_white',
        height=400,
        showlegend=True
    )
    
    return fig

def create_hourly_analysis_chart(hourly_data):
    """Create an interactive hourly outage analysis chart."""
    if hourly_data.empty:
        return None
    
    fig = make_subplots(
        rows=2, cols=1,
        subplot_titles=('Outage Count by Hour', 'Total Downtime by Hour'),
        vertical_spacing=0.1
    )
    
    # Outage count chart
    fig.add_trace(
        go.Bar(
            x=hourly_data.index,
            y=hourly_data['Count'],
            name='Outage Count',
            marker_color='#2ca02c',
            hovertemplate='<b>Hour %{x}</b><br>Count: %{y}<extra></extra>'
        ),
        row=1, col=1
    )
    
    # Downtime chart
    fig.add_trace(
        go.Bar(
            x=hourly_data.index,
            y=hourly_data['Total_Downtime_Min'],
            name='Total Downtime (min)',
            marker_color='#17a2b8',
            hovertemplate='<b>Hour %{x}</b><br>Downtime: %{y:.1f} min<extra></extra>'
        ),
        row=2, col=1
    )
    
    fig.update_layout(
        title="Hourly Outage Analysis",
        template='plotly_white',
        height=600,
        showlegend=False
    )
    
    fig.update_xaxes(title_text="Hour of Day", row=2, col=1)
    fig.update_yaxes(title_text="Count", row=1, col=1)
    fig.update_yaxes(title_text="Downtime (minutes)", row=2, col=1)
    
    return fig

# ----------------------------
# Custom CSS for Modern UI
# ----------------------------

def load_custom_css():
    st.markdown("""
    <style>
    /* Main theme colors */
    :root {
        --primary-color: #1f77b4;
        --secondary-color: #ff7f0e;
        --success-color: #2ca02c;
        --warning-color: #d62728;
        --info-color: #17a2b8;
        --light-bg: #f8f9fa;
        --dark-bg: #343a40;
        --border-color: #dee2e6;
    }
    
    /* Main container styling */
    .main .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
        max-width: 1200px;
    }
    
    /* Header styling */
    .main-header {
        background: linear-gradient(90deg, var(--primary-color), var(--secondary-color));
        color: white;
        padding: 2rem;
        border-radius: 10px;
        margin-bottom: 2rem;
        text-align: center;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    }
    
    .main-header h1 {
        margin: 0;
        font-size: 2.5rem;
        font-weight: 700;
    }
    
    .main-header p {
        margin: 0.5rem 0 0 0;
        font-size: 1.1rem;
        opacity: 0.9;
    }
    
    /* Sidebar styling */
    .css-1d391kg {
        background-color: var(--light-bg);
    }
    
    /* Metric cards */
    .metric-card {
        background: white;
        padding: 1.5rem;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        border-left: 4px solid var(--primary-color);
        margin-bottom: 1rem;
    }
    
    /* Success/Error messages */
    .stSuccess {
        background-color: #d4edda;
        border: 1px solid #c3e6cb;
        color: #155724;
        border-radius: 8px;
        padding: 1rem;
    }
    
    .stError {
        background-color: #f8d7da;
        border: 1px solid #f5c6cb;
        color: #721c24;
        border-radius: 8px;
        padding: 1rem;
    }
    
    /* Form styling */
    .stForm {
        background: white;
        padding: 2rem;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        margin-bottom: 2rem;
    }
    
    /* Button styling */
    .stButton > button {
        background: linear-gradient(45deg, var(--primary-color), var(--secondary-color));
        color: white;
        border: none;
        border-radius: 8px;
        padding: 0.5rem 2rem;
        font-weight: 600;
        transition: all 0.3s ease;
    }
    
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
    }
    
    /* Dataframe styling */
    .dataframe {
        border-radius: 8px;
        overflow: hidden;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
    }
    
    /* Chart containers */
    .chart-container {
        background: white;
        padding: 1.5rem;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        margin-bottom: 2rem;
    }
    
    /* Navigation tabs */
    .nav-tabs {
        background: var(--light-bg);
        border-radius: 10px;
        padding: 0.5rem;
        margin-bottom: 2rem;
    }
    
    /* Responsive design */
    @media (max-width: 768px) {
        .main-header h1 {
            font-size: 2rem;
        }
        .main-header p {
            font-size: 1rem;
        }
    }
    
    /* Loading spinner */
    .loading {
        display: inline-block;
        width: 20px;
        height: 20px;
        border: 3px solid rgba(255,255,255,.3);
        border-radius: 50%;
        border-top-color: #fff;
        animation: spin 1s ease-in-out infinite;
    }
    
    @keyframes spin {
        to { transform: rotate(360deg); }
    }
    
    /* Custom scrollbar */
    ::-webkit-scrollbar {
        width: 8px;
    }
    
    ::-webkit-scrollbar-track {
        background: #f1f1f1;
        border-radius: 4px;
    }
    
    ::-webkit-scrollbar-thumb {
        background: var(--primary-color);
        border-radius: 4px;
    }
    
    ::-webkit-scrollbar-thumb:hover {
        background: var(--secondary-color);
    }
    </style>
    """, unsafe_allow_html=True)

# ----------------------------
# Authentication System
# ----------------------------

def check_password():
    """Returns `True` if the user had the correct password."""
    
    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if st.session_state["password"] == "technical":
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # don't store password
        else:
            st.session_state["password_correct"] = False

    # Return True if the password is validated initially.
    if st.session_state.get("password_correct", False):
        return True

    # Show input for password.
    st.markdown("""
    <div style="text-align: center; padding: 2rem;">
        <h1>🔐 Access Control</h1>
        <p>Please enter the password to access the Transmitter Outage Monitoring System</p>
    </div>
    """, unsafe_allow_html=True)
    
    st.text_input(
        "Password", type="password", on_change=password_entered, key="password"
    )
    if "password_correct" in st.session_state:
        st.error("😕 Password incorrect")
    return False

# ----------------------------
# Streamlit UI
# ----------------------------

st.set_page_config(
    page_title="📡 Transmitter Outage Monitor", 
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        'Get Help': 'https://docs.streamlit.io/',
        'Report a bug': "https://github.com/streamlit/streamlit/issues",
        'About': "# Transmitter Outage Monitoring System\nBuilt with Streamlit for efficient outage tracking and analysis."
    }
)

# Load custom CSS
load_custom_css()

# Check password before showing the main application
if not check_password():
    st.stop()  # Do not continue if check_password is not True.

# Modern header with gradient background
st.markdown("""
<div class="main-header">
    <h1>📡 Transmitter Outage Monitoring System</h1>
    <p>Advanced outage tracking and reliability analysis for broadcast operations</p>
</div>
""", unsafe_allow_html=True)



# Enhanced sidebar with better styling
st.sidebar.markdown("### 🎛️ Control Panel")

# Sidebar menu with enhanced options
menu = st.sidebar.radio(
    "📋 Navigation", 
    ["📝 Log Outage", "✏️ Edit Records", "📊 View Summary", "📈 Analytics Dashboard", "📤 Data Export"],
    help="Select a section to navigate to"
)

# Add system status in sidebar
st.sidebar.markdown("---")
st.sidebar.markdown("### 📊 System Status")

# Load data to check system status
df = load_data()

if not df.empty:
    # Calculate current system health
    summary_data = build_comprehensive_summary(df)
    ytd_summary = summary_data['ytd']
    
    if ytd_summary:
        availability = ytd_summary['ytd_availability_%']
        if availability >= 99.5:
            status_color = "🟢"
            status_text = "Excellent"
        elif availability >= 99.0:
            status_color = "🟡"
            status_text = "Good"
        else:
            status_color = "🔴"
            status_text = "Needs Attention"
        
        st.sidebar.markdown(f"**Status**: {status_color} {status_text}")
        st.sidebar.metric("YTD Availability", f"{availability:.2f}%")
        st.sidebar.metric("YTD Failures", f"{ytd_summary['failure_count']}")
    else:
        st.sidebar.markdown("**Status**: 🟡 No YTD data")
else:
    st.sidebar.markdown("**Status**: 🔵 No data")

st.sidebar.markdown("---")
st.sidebar.info("""
**Broadcast Hours**: 4:30 AM - 10:00 PM  
**Daily Broadcast**: 17.5 hours  
**Target Availability**: >99%
""")

# Subtle credit
st.sidebar.markdown("---")
st.sidebar.markdown(
    '<div style="text-align: center; font-size: 0.8em; color: #666; margin-top: 20px;">Created by Engr Dod</div>', 
    unsafe_allow_html=True
)

# Add search and filter options in sidebar
if menu in ["✏️ Edit Records", "📊 View Summary", "📈 Analytics Dashboard"]:
    st.sidebar.markdown("### 🔍 Filters")
    year_filter = st.sidebar.selectbox("Filter by Year", ["All"] + list(range(2020, 2030)))
    month_filter = st.sidebar.selectbox("Filter by Month", ["All"] + [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ])

# ----------------------------
# Log Outage Page
# ----------------------------
if menu == "📝 Log Outage":
    st.subheader("➕ Log a new outage")
    
    # Show recent outages for reference (last 3 months)
    if not df.empty:
        st.markdown("### 📋 Recent Outages (Last 3 Months)")
        # Filter to last 3 months
        three_months_ago = datetime.now() - timedelta(days=90)
        recent_outages = df[df['Date'] >= three_months_ago][['Date', 'Start Time', 'End Time', 'Failure Type', 'Downtime (hh:mm)']]
        
        if not recent_outages.empty:
            # Show last 10 records from the filtered data
            recent_outages = recent_outages.tail(10)
            st.dataframe(recent_outages, use_container_width=True)
        else:
            st.info("No outages recorded in the last 3 months.")
        st.markdown("---")

    with st.form("log_form", clear_on_submit=False):
        st.markdown("### 📝 Outage Details")
        
        col1, col2 = st.columns(2)
        
        with col1:
            date = st.date_input(
                "📅 Date", 
                value=datetime.now().date(),
                help="Select the date when the outage occurred"
            )
            failure_type = st.selectbox(
                "🔧 Failure Type", 
                ["Power", "Transmitter", "Link", "Antenna", "Audio", "Other"],
                help="Select the primary cause of the outage"
            )
        
        with col2:
            start_time = st.time_input(
                "🕐 Start Time", 
                step=60, 
                help="When did the outage begin? (Must be within broadcast hours: 4:30 AM - 10:00 PM)"
            )
            end_time = st.time_input(
                "🕐 End Time", 
                step=60, 
                help="When did the outage end? (Must be within broadcast hours: 4:30 AM - 10:00 PM)"
            )
        
        remarks = st.text_area(
            "📝 Remarks", 
            placeholder="Additional details about the outage, root cause, resolution steps, etc...",
            help="Optional: Provide additional context about the outage"
        )
        
        # Show calculated downtime preview with enhanced styling
        if start_time and end_time:
            start_dt = datetime.combine(date, start_time)
            end_dt = datetime.combine(date, end_time)
            downtime_minutes = int((end_dt - start_dt).total_seconds() / 60)
            hours, minutes = divmod(downtime_minutes, 60)
            downtime_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
            
            # Color code the downtime preview
            if downtime_minutes <= 30:
                st.success(f"📊 **Calculated Downtime**: {downtime_str} (Short outage)")
            elif downtime_minutes <= 120:
                st.warning(f"📊 **Calculated Downtime**: {downtime_str} (Medium outage)")
            else:
                st.error(f"📊 **Calculated Downtime**: {downtime_str} (Long outage)")

        submitted = st.form_submit_button("💾 Save Outage", use_container_width=True, type="primary")
        
        if submitted:
            # Validate inputs
            required_valid, required_msg = validate_required_fields(date, start_time, end_time, failure_type)
            if not required_valid:
                st.error(f"Validation Error: {required_msg}")
            else:
                time_valid, time_msg = validate_time_input(start_time, end_time, date)
                if not time_valid:
                    st.error(f"Validation Error: {time_msg}")
                else:
                    # Check for duplicates
                    if check_duplicate_entry(df, date, start_time, end_time):
                        st.error("Duplicate Entry: An outage record already exists for this date and time period.")
                    else:
                        # Create new record
                        new_record = {
                            "Date": date,
                            "Start Time": start_time,
                            "End Time": end_time,
                            "Downtime (minutes)": downtime_minutes,
                            "Downtime (hh:mm)": downtime_str,
                            "Failure Type": failure_type,
                            "Remarks": remarks
                        }

                        # Show progress indicator
                        with st.spinner("💾 Saving outage data..."):
                            df = pd.concat([df, pd.DataFrame([new_record])], ignore_index=True)
                            if save_data(df):
                                st.success("New outage record has been created successfully.")
                                # Clear cache and refresh data
                                load_data.clear()
                                # Add a delay to show the message before refresh
                                time.sleep(2)
                                st.rerun()
                            else:
                                st.error("Unable to save outage record. Please verify your input and try again.")

# ----------------------------
# Edit Records Page
# ----------------------------
elif menu == "✏️ Edit Records":
    st.subheader("✏️ Edit or Delete outage records")

    if df.empty:
        st.info("📭 No records found. Log some outages first!")
    else:
        # Apply filters
        filtered_df = df.copy()
        if year_filter != "All":
            filtered_df = filtered_df[filtered_df['Date'].dt.year == year_filter]
        if month_filter != "All":
            filtered_df = filtered_df[filtered_df['Date'].dt.strftime('%B') == month_filter]
        
        st.markdown(f"**Total Records:** {len(filtered_df)} (Filtered from {len(df)} total)")
        
        # Enhanced dataframe display
        st.dataframe(
            filtered_df, 
            use_container_width=True,
            hide_index=True
        )
        
        st.markdown("---")
        
        # Record selection
        col1, col2 = st.columns([2, 1])
        with col1:
            record_index = st.number_input(
                "📝 Enter row index to edit/delete", 
                min_value=0, 
                max_value=len(filtered_df)-1, 
                step=1,
                help="Use the index number from the dataframe above"
            )
        with col2:
            action = st.radio("🔧 Action", ["Edit", "Delete"])

        if len(filtered_df) > 0:
            # Show selected record details
            selected_record = filtered_df.iloc[record_index]
            st.markdown("### 📋 Selected Record")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Date", selected_record['Date'].strftime('%Y-%m-%d'))
                st.metric("Start Time", str(selected_record['Start Time']))
            with col2:
                st.metric("End Time", str(selected_record['End Time']))
                st.metric("Downtime", selected_record['Downtime (hh:mm)'])
            with col3:
                st.metric("Failure Type", selected_record['Failure Type'])
                st.metric("Minutes", selected_record['Downtime (minutes)'])

            if action == "Edit":
                st.markdown("### ✏️ Edit Record")
                with st.form("edit_form"):
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        date = st.date_input("📅 Date", value=selected_record['Date'].date())
                        failure_type = st.selectbox(
                            "🔧 Failure Type", 
                            ["Power", "Transmitter", "Link", "Antenna", "Audio", "Other"],
                            index=["Power", "Transmitter", "Link", "Antenna", "Audio", "Other"].index(selected_record['Failure Type']) if selected_record['Failure Type'] in ["Power", "Transmitter", "Link", "Antenna", "Audio", "Other"] else 0
                        )
                    
                    with col2:
                        start_time = st.time_input(
                            "🕐 Start Time",
                            value=pd.to_datetime(str(selected_record['Start Time'])).time(),
                            step=60
                        )
                        end_time = st.time_input(
                            "🕐 End Time",
                            value=pd.to_datetime(str(selected_record['End Time'])).time(),
                            step=60
                        )
                    
                    remarks = st.text_area("📝 Remarks", value=selected_record['Remarks'])

                    submitted = st.form_submit_button("💾 Update Record", use_container_width=True)
                    if submitted:
                        # Validate inputs
                        required_valid, required_msg = validate_required_fields(date, start_time, end_time, failure_type)
                        if not required_valid:
                            st.error(f"Validation Error: {required_msg}")
                        else:
                            time_valid, time_msg = validate_time_input(start_time, end_time, date)
                            if not time_valid:
                                st.error(f"Validation Error: {time_msg}")
                            else:
                                start_dt = datetime.combine(date, start_time)
                                end_dt = datetime.combine(date, end_time)
                                downtime_minutes = int((end_dt - start_dt).total_seconds() / 60)
                                hours, minutes = divmod(downtime_minutes, 60)
                                downtime_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

                                # Find the actual index in the original dataframe
                                actual_index = df.index[df.index == filtered_df.index[record_index]].tolist()[0]
                                
                                # Update the record with correct field mapping
                                df.loc[actual_index, 'Date'] = date
                                df.loc[actual_index, 'Start Time'] = start_time
                                df.loc[actual_index, 'End Time'] = end_time
                                df.loc[actual_index, 'Downtime (minutes)'] = downtime_minutes
                                df.loc[actual_index, 'Downtime (hh:mm)'] = downtime_str
                                df.loc[actual_index, 'Failure Type'] = failure_type
                                df.loc[actual_index, 'Remarks'] = remarks
                                
                                if save_data(df):
                                    st.success("Record has been updated successfully.")
                                    # Clear cache and refresh data
                                    load_data.clear()
                                    # Add a delay to show the message before refresh
                                    time.sleep(2)
                                    st.rerun()
                                else:
                                    st.error("Unable to update record. Please check your input and try again.")

            elif action == "Delete":
                st.markdown("### 🗑️ Delete Record")
                st.warning("Are you sure you want to delete this record? This action cannot be undone.")
                
                if st.button("🗑️ Confirm Delete", type="primary"):
                    # Find the actual index in the original dataframe
                    actual_index = df.index[df.index == filtered_df.index[record_index]].tolist()[0]
                    df = df.drop(actual_index).reset_index(drop=True)
                    
                    if save_data(df):
                        st.success("Record has been deleted successfully.")
                        # Clear cache and refresh data
                        load_data.clear()
                        # Add a delay to show the message before refresh
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.error("Unable to delete record. Please try again.")

# ----------------------------
# View Summary Page
# ----------------------------
elif menu == "📊 View Summary":
    st.subheader("📊 Availability Summary")
    
    if df.empty:
        st.info("📭 No data available. Log some outages first!")
    else:
        # Get summary data
        summary_data = build_comprehensive_summary(df)
        monthly_summary = summary_data['monthly']
        yearly_summary = summary_data['yearly']
        ytd_summary = summary_data['ytd']
        
        # Apply filters
        if year_filter != "All":
            monthly_summary = monthly_summary[monthly_summary['Year'] == year_filter]
            yearly_summary = yearly_summary[yearly_summary['Year'] == year_filter]
        if month_filter != "All":
            monthly_summary = monthly_summary[monthly_summary['Month'] == month_filter]
        
        # Key Metrics Overview
        st.markdown("### 📈 Key Performance Indicators")
        
        # Year-to-Date Availability (most prominent)
        if ytd_summary:
            st.markdown("#### 🎯 Year-to-Date Performance")
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric(
                    "YTD Availability", 
                    f"{ytd_summary['ytd_availability_%']:.2f}%",
                    delta=f"{ytd_summary['ytd_availability_%'] - 99:.2f}%" if ytd_summary['ytd_availability_%'] != 99 else None,
                    help=f"Availability from Jan 1, {ytd_summary['current_year']} to today"
                )
            with col2:
                st.metric(
                    "Days Elapsed", 
                    f"{ytd_summary['days_elapsed']}",
                    help="Days from January 1st to current date"
                )
            with col3:
                st.metric(
                    "YTD Failures", 
                    f"{ytd_summary['failure_count']}",
                    help="Total failures from January 1st to current date"
                )
            with col4:
                st.metric(
                    "YTD Downtime", 
                    f"{ytd_summary['total_downtime_minutes'] / 60:.1f}h",
                    help="Total downtime from January 1st to current date"
                )
            st.markdown("---")
        
        # Latest Month Performance
        if not monthly_summary.empty:
            st.markdown("#### 📅 Latest Month Performance")
            latest_month = monthly_summary.iloc[-1]
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Latest Month Availability", f"{latest_month['Availability_%']:.2f}%", 
                         delta=f"{latest_month['Availability_%'] - 99:.2f}%" if latest_month['Availability_%'] != 99 else None)
            with col2:
                st.metric("Latest Month Failures", int(latest_month['Failure_Count']))
            with col3:
                st.metric("Latest Month Downtime", f"{latest_month['Total_Downtime_Minutes'] / 60:.1f}h")
        
        # Monthly Summary
        st.markdown("### 📅 Monthly Summary")
        if not monthly_summary.empty:
            # Format the dataframe for better display
            display_monthly = monthly_summary.copy()
            display_monthly['Availability_%'] = display_monthly['Availability_%'].round(2)
            display_monthly['Total_Downtime_Minutes'] = display_monthly['Total_Downtime_Minutes'].astype(int)
            
            st.dataframe(display_monthly, use_container_width=True)
            
            # Interactive Charts
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("#### 📈 Availability Trend")
                availability_chart = create_availability_chart(monthly_summary)
                if availability_chart:
                    st.plotly_chart(availability_chart, use_container_width=True)
                else:
                    st.info("No data available for availability chart")
            
            with col2:
                st.markdown("#### 📊 Failure Count Trend")
                failure_trend_fig = go.Figure()
                failure_trend_fig.add_trace(go.Bar(
                    x=monthly_summary['Month'],
                    y=monthly_summary['Failure_Count'],
                    name='Failure Count',
                    marker_color='#ff7f0e',
                    hovertemplate='<b>%{x}</b><br>Failures: %{y}<extra></extra>'
                ))
                failure_trend_fig.update_layout(
                    title="Monthly Failure Count",
                    xaxis_title="Month",
                    yaxis_title="Failure Count",
                    template='plotly_white',
                    height=400,
                    showlegend=False
                )
                st.plotly_chart(failure_trend_fig, use_container_width=True)
        else:
            st.info("No monthly data available for the selected filters.")
        
        # Yearly Summary
        st.markdown("### 📆 Yearly Summary")
        
        # Show current year YTD information prominently
        if ytd_summary:
            st.markdown("#### 🎯 Current Year (Year-to-Date)")
            col1, col2 = st.columns(2)
            with col1:
                st.metric(
                    "YTD Availability", 
                    f"{ytd_summary['ytd_availability_%']:.2f}%",
                    delta=f"{ytd_summary['ytd_availability_%'] - 99:.2f}%" if ytd_summary['ytd_availability_%'] != 99 else None
                )
                st.metric("Days Elapsed", f"{ytd_summary['days_elapsed']}")
            with col2:
                st.metric("YTD Failures", f"{ytd_summary['failure_count']}")
                st.metric("YTD Downtime", f"{ytd_summary['total_downtime_minutes'] / 60:.1f}h")
            
            # Show projected full year availability
            if ytd_summary['days_elapsed'] < 365:
                days_remaining = 365 - ytd_summary['days_elapsed']
                projected_remaining_downtime = (ytd_summary['total_downtime_minutes'] / ytd_summary['days_elapsed']) * days_remaining
                projected_total_downtime = ytd_summary['total_downtime_minutes'] + projected_remaining_downtime
                projected_availability = ((365 * BROADCAST_HOURS_PER_DAY * 60 - projected_total_downtime) / (365 * BROADCAST_HOURS_PER_DAY * 60)) * 100
                
                st.info(f"📊 **Projected Full Year Availability**: {projected_availability:.2f}% (based on current trend)")
            
            st.markdown("---")
        
        # Show historical yearly data
        if not yearly_summary.empty:
            st.markdown("#### 📈 Historical Yearly Data")
            # Format the dataframe for better display
            display_yearly = yearly_summary.copy()
            display_yearly['Availability_%'] = display_yearly['Availability_%'].round(2)
            display_yearly['Max_Downtime_hours'] = display_yearly['Max_Downtime_hours'].round(1)
            display_yearly['Min_Downtime_hours'] = display_yearly['Min_Downtime_hours'].round(1)
            
            st.dataframe(display_yearly, use_container_width=True)
        else:
            st.info("No historical yearly data available for the selected filters.")

# ----------------------------
# Analytics Dashboard Page
# ----------------------------
elif menu == "📈 Analytics Dashboard":
    st.subheader("📊 Advanced Analytics Dashboard")
    
    if df.empty:
        st.info("📭 No data available. Log some outages first!")
    else:
        # Apply filters
        filtered_df = df.copy()
        if year_filter != "All":
            filtered_df = filtered_df[filtered_df['Date'].dt.year == year_filter]
        if month_filter != "All":
            filtered_df = filtered_df[filtered_df['Date'].dt.strftime('%B') == month_filter]
        
        if filtered_df.empty:
            st.info("No data available for the selected filters.")
        else:
            # Failure Type Analysis
            st.markdown("### 🔧 Failure Type Analysis")
            failure_analysis = filtered_df.groupby('Failure Type').agg({
                'Downtime (minutes)': ['count', 'sum', 'mean'],
                'Date': 'nunique'
            }).round(2)
            failure_analysis.columns = ['Count', 'Total_Downtime_Min', 'Avg_Downtime_Min', 'Days_Affected']
            failure_analysis['Total_Downtime_Hours'] = (failure_analysis['Total_Downtime_Min'] / 60).round(1)
            failure_analysis['Avg_Downtime_Hours'] = (failure_analysis['Avg_Downtime_Min'] / 60).round(1)
            
            col1, col2 = st.columns(2)
            with col1:
                st.dataframe(failure_analysis, use_container_width=True)
            with col2:
                st.markdown("#### 📊 Failure Distribution")
                failure_chart = create_failure_analysis_chart(failure_analysis)
                if failure_chart:
                    st.plotly_chart(failure_chart, use_container_width=True)
                else:
                    st.info("No failure data available")
            
            # Time-based Analysis
            st.markdown("### ⏰ Time-based Analysis")
            filtered_df['Hour'] = pd.to_datetime(filtered_df['Start Time']).dt.hour
            hourly_analysis = filtered_df.groupby('Hour').agg({
                'Downtime (minutes)': ['count', 'sum']
            }).round(2)
            hourly_analysis.columns = ['Count', 'Total_Downtime_Min']
            
            st.markdown("#### 🕐 Hourly Outage Analysis")
            hourly_chart = create_hourly_analysis_chart(hourly_analysis)
            if hourly_chart:
                st.plotly_chart(hourly_chart, use_container_width=True)
            else:
                st.info("No hourly data available")
            
            # Trend Analysis
            st.markdown("### 📈 Trend Analysis")
            filtered_df['Date'] = pd.to_datetime(filtered_df['Date'])
            daily_analysis = filtered_df.groupby('Date').agg({
                'Downtime (minutes)': ['count', 'sum']
            }).round(2)
            daily_analysis.columns = ['Daily_Failures', 'Daily_Downtime_Min']
            daily_analysis['Daily_Downtime_Hours'] = (daily_analysis['Daily_Downtime_Min'] / 60).round(1)
            
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("#### 📅 Daily Failure Count")
                daily_failures_fig = go.Figure()
                daily_failures_fig.add_trace(go.Scatter(
                    x=daily_analysis.index,
                    y=daily_analysis['Daily_Failures'],
                    mode='lines+markers',
                    name='Daily Failures',
                    line=dict(color='#2ca02c', width=2),
                    marker=dict(size=6),
                    hovertemplate='<b>%{x}</b><br>Failures: %{y}<extra></extra>'
                ))
                daily_failures_fig.update_layout(
                    title="Daily Failure Count",
                    xaxis_title="Date",
                    yaxis_title="Failure Count",
                    template='plotly_white',
                    height=400,
                    showlegend=False
                )
                st.plotly_chart(daily_failures_fig, use_container_width=True)
            
            with col2:
                st.markdown("#### ⏱️ Daily Downtime")
                downtime_chart = create_downtime_trend_chart(daily_analysis)
                if downtime_chart:
                    st.plotly_chart(downtime_chart, use_container_width=True)
                else:
                    st.info("No daily downtime data available")

# ----------------------------
# Data Export Page
# ----------------------------
elif menu == "📤 Data Export":
    st.subheader("📤 Data Export & Backup")
    
    if df.empty:
        st.info("📭 No data available to export.")
    else:
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("### 📊 Export Options")
            
            # Raw Data Export
            st.markdown("#### 📋 Raw Data")
            csv_data = df.to_csv(index=False)
            st.download_button(
                label="📥 Download CSV",
                data=csv_data,
                file_name=f"outage_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv"
            )
            
            # Summary Data Export
            st.markdown("#### 📈 Summary Data")
            summary_data = build_comprehensive_summary(df)
            
            if not summary_data['monthly'].empty:
                monthly_csv = summary_data['monthly'].to_csv(index=False)
                st.download_button(
                    label="📥 Download Monthly Summary",
                    data=monthly_csv,
                    file_name=f"monthly_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv"
                )
            
            if not summary_data['yearly'].empty:
                yearly_csv = summary_data['yearly'].to_csv(index=False)
                st.download_button(
                    label="📥 Download Yearly Summary",
                    data=yearly_csv,
                    file_name=f"yearly_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv"
                )
        
        with col2:
            st.markdown("### 📊 Data Statistics")
            st.metric("Total Records", len(df))
            st.metric("Date Range", f"{df['Date'].min().strftime('%Y-%m-%d')} to {df['Date'].max().strftime('%Y-%m-%d')}")
            st.metric("Total Downtime", f"{df['Downtime (minutes)'].sum() / 60:.1f} hours")
            st.metric("Average Downtime", f"{df['Downtime (minutes)'].mean():.1f} minutes")
            
            # Add YTD statistics if available
            summary_data = build_comprehensive_summary(df)
            ytd_summary = summary_data['ytd']
            if ytd_summary:
                st.markdown("### 🎯 Year-to-Date Statistics")
                st.metric("YTD Availability", f"{ytd_summary['ytd_availability_%']:.2f}%")
                st.metric("YTD Days", f"{ytd_summary['days_elapsed']}")
                st.metric("YTD Failures", f"{ytd_summary['failure_count']}")
                st.metric("YTD Downtime", f"{ytd_summary['total_downtime_minutes'] / 60:.1f}h")
            
            st.markdown("### 🔧 Failure Types")
            failure_counts = df['Failure Type'].value_counts()
            for failure_type, count in failure_counts.items():
                st.write(f"• **{failure_type}**: {count} occurrences")

