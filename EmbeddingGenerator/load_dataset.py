from dotenv import load_dotenv
import os
from pymongo import MongoClient
from decouple import config
import configparser
import json
from openai import OpenAI
import pandas as pd
import logging

# Configure logging
logging.basicConfig(filename='embedding_generation.log', level=logging.ERROR)

load_dotenv()
config = configparser.ConfigParser()
config.read('openai.ini')

# Set up MongoDB connection
try:
    print("Establishing MongoDB connection...")
    client = MongoClient(os.getenv("MONGO_URI"))
    db = client.saibabasayings
    collection = db.paragraphs
    print("MongoDB connection established.")
except Exception as e:
    logging.error(f"Error establishing MongoDB connection: {e}")

# Set up OpenAI client
try:
    print("Setting up OpenAI client...")
    openai_client = OpenAI(api_key=config['OpenAI']['api_key'])
    print("OpenAI client setup complete.")
except Exception as e:
    logging.error(f"Error setting up OpenAI client: {e}")

# Load the JSON file into a Python list
try:
    print("Loading JSON file...")
    with open("../../ask-sai-baba/Web scraper/data_ongoing_v11.json", "r") as f:
        dataset_list = json.load(f)
    print("JSON file loaded.")
except Exception as e:
    logging.error(f"Error loading JSON file: {e}")

# Convert the training data to a pandas DataFrame
try:
    print("Converting data to DataFrame...")
    dataset_df = pd.DataFrame(dataset_list)
    print("Data converted to DataFrame.")
except Exception as e:
    logging.error(f"Error converting data to DataFrame: {e}")

# Display the first 5 rows of the DataFrame
print(dataset_df.head(5))

