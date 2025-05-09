import streamlit as st
from pathlib import Path
from langchain.agents import create_sql_agent
from langchain.sql_database import SQLDatabase
from langchain.agents.agent_types import AgentType
from langchain.callbacks import StreamlitCallbackHandler
from langchain.agents.agent_toolkits import SQLDatabaseToolkit
from sqlalchemy import create_engine
import sqlite3
from langchain_groq import ChatGroq
from urllib.parse import quote_plus
import pymysql
import pandas as pd
from datetime import datetime
from api_key import groq_api_key
import glob
import os

st.set_page_config(page_title="LangChain: Chat with SQL DB", page_icon="🦜")
st.title("🦜 LangChain: Chat with SQL DB")

LOCALDB = "USE_LOCALDB"
MYSQL = "USE_MYSQL"

def get_available_databases(base_path="database/local"):
    """Find all SQLite database files in the specified directory"""
    project_root = Path(__file__).parent
    search_path = project_root / base_path
    
    # Find all database files with common SQLite extensions
    db_files = []
    for ext in ['.db', '.sqlite', '.sqlite3']:
        db_files.extend(list(search_path.glob(f"*{ext}")))
    
    # Convert to relative paths for display
    return [db.relative_to(project_root) for db in db_files]

radio_opt = ["Use SQLite 3 Database", "Connect to your MySQL Database"]

selected_opt = st.sidebar.radio(label="Choose the DB which you want to chat", options=radio_opt)

if radio_opt.index(selected_opt) == 1:
    db_uri = MYSQL
    mysql_host = st.sidebar.text_input("Provide MySQL Host")
    mysql_user = st.sidebar.text_input("MySQL User")
    mysql_password = st.sidebar.text_input("MySQL Password", type="password")
    mysql_db = st.sidebar.text_input("MySQL Database")
else:
    db_uri = LOCALDB
    # Get list of available databases
    available_dbs = get_available_databases()
    
    if not available_dbs:
        st.error("No SQLite databases found in the database/local directory!")
        st.stop()
    
    selected_db = st.sidebar.selectbox(
        "Select SQLite Database",
        options=available_dbs,
        format_func=lambda x: x.name
    )

api_key = st.sidebar.text_input(label="Groq API Key", type="password")

if not db_uri:
    st.info("Please enter the database information and URI.")

if not api_key:
    st.info("Please add the Groq API key.")

# LLM Model (You can use your own model here)
llm = ChatGroq(groq_api_key=groq_api_key, 
               model_name="Llama-3.3-70b-versatile", 
               temperature=0.0,
               top_p=1.0,
               streaming=True)

def configure_db(db_uri, selected_db=None, mysql_host=None, mysql_user=None, mysql_password=None, mysql_db=None):
    if db_uri == LOCALDB:
        # SQLite setup with dynamic database selection
        if not selected_db:
            st.error("No database selected")
            st.stop()
            
        dbfilepath = (Path(__file__).parent / selected_db).absolute()
        st.write(f"Connecting to SQLite database: {dbfilepath}")
        creator = lambda: sqlite3.connect(f"file:{dbfilepath}?mode=ro", uri=True)
        
        try:
            db = SQLDatabase(create_engine("sqlite:///", creator=creator))
            return db
        except Exception as e:
            st.error(f"SQLite Connection Error: {str(e)}")
            st.stop()

    elif db_uri == MYSQL:
        if not (mysql_host and mysql_user and mysql_password and mysql_db):
            st.error("Please provide all MySQL connection details.")
            st.stop()

        try:
            # Clean and validate host
            mysql_host = mysql_host.strip()
            if '@' in mysql_host:
                raise ValueError("Host cannot contain '@' symbol.")
            
            # Handle host and port
            if ":" in mysql_host:
                host, port = mysql_host.rsplit(":", 1)
                if not port.isdigit():
                    raise ValueError("Port must be numeric.")
            else:
                host = mysql_host
                port = "3306"  # Default MySQL port
            
            # URL encode password to handle special characters
            encoded_password = quote_plus(mysql_password)
            
            # Build MySQL connection string
            connection_string = f"mysql+pymysql://{mysql_user}:{encoded_password}@{host}:{port}/{mysql_db}"
            st.write(f"Connecting to MySQL with connection string: {connection_string}")

            # Create SQLAlchemy engine
            engine = create_engine(connection_string)

            # Test the connection using a simple query:
            with engine.connect() as conn:
                st.write("Successfully connected to MySQL.")
                # We don't need to execute a SELECT query here, we just want to confirm the connection works.

            # Return the database connection wrapped with SQLDatabase
            return SQLDatabase(engine)

        except Exception as e:
            st.error(f"MySQL Connection Error: {str(e)}")
            st.stop()

# Main database connection logic
if db_uri == MYSQL:
    db = configure_db(db_uri=db_uri,
                      mysql_host=mysql_host,
                      mysql_user=mysql_user,
                      mysql_password=mysql_password,
                      mysql_db=mysql_db)
else:
    db = configure_db(db_uri, selected_db=selected_db)

try:
    # Get schema of the database
    schema = db.get_table_names()
    st.write("Database Schema:", schema)  # Log schema for debugging
except Exception as e:
    st.error(f"Error retrieving schema: {str(e)}")
    schema = []

# Define a function to format the query and enhance its clarity
def format_query_for_agent(user_query, schema):
    # Provide schema context to the agent
    schema_hint = f"Database schema includes tables: {', '.join(schema)}. Please provide a detailed, formatted answer, including the relevant data in a human-readable way."

    # Append the schema hint to the user's query to help the agent understand the required output
    return f"{schema_hint}\nAnswer the question: {user_query}"

# Initialize toolkit and agent for interacting with the database
toolkit = SQLDatabaseToolkit(db=db, llm=llm)
agent = create_sql_agent (
    llm=llm,
    toolkit=toolkit,
    verbose=True,
    agent_type=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
    handle_parsing_errors=True 
)

# Initialize session state for storing chat messages and chat history
if "messages" not in st.session_state or st.sidebar.button("Clear message history"):
    st.session_state["messages"] = [{"role": "assistant", "content": "How can I help you?"}]
    
# Initialize chat_history if it doesn't exist
if "chat_history" not in st.session_state:
    st.session_state["chat_history"] = []

# Display chat messages
for msg in st.session_state.messages:
    st.chat_message(msg["role"]).write(msg["content"])

# Accept user query and process it
user_query = st.chat_input(placeholder="Ask anything from the database")

if user_query:
    formatted_query = format_query_for_agent(user_query, schema)
    st.session_state.messages.append({"role": "user", "content": user_query})
    st.chat_message("user").write(user_query)

    with st.chat_message("assistant"):
        streamlit_callback = StreamlitCallbackHandler(st.container())
        response = agent.run(formatted_query, callbacks=[streamlit_callback])
        st.session_state.messages.append({"role": "assistant", "content": response})
        st.write(response)

        # Save the response along with the SQL queries that were executed (SQL query chain)
        st.session_state.chat_history.append({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # Timestamp for when the query is made
            "user_query": user_query,
            "sql_query": formatted_query,  # You may want to log the exact SQL query here
            "response": response
        })

        st.write(response)

# Saving full chat history in the session state
def to_csv(chat_history):
    # Convert the chat history to a DataFrame
    df = pd.DataFrame(chat_history)
    # Convert dataframe to CSV without saving to disk
    csv = df.to_csv(index=False).encode()
    return csv

# Provide the option to download the full chat history as CSV
csv_data = to_csv(st.session_state.chat_history)
st.download_button(
    label="Download Full Chat History with SQL Queries as CSV",
    data=csv_data,
    file_name="chat_history.csv",
    mime="text/csv"
)
