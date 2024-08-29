from dotenv import load_dotenv
import os
from pymongo import MongoClient
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


def get_embedding(text):
    """Generate an embedding for the given text using OpenAI's API."""

    # Check for valid input
    if not text or not isinstance(text, str):
        return None

    try:
        # Call OpenAI API to get the embedding
        embedding = openai_client.embeddings.create(
            input=text, model="text-embedding-3-large").data[0].embedding
        return embedding
    except Exception as e:
        logging.error(f"Error generating embedding: {e}")
        return None


# Remove rows with empty strings in the 'content' column
try:
    print("Removing rows with empty strings in 'content' column...")
    dataset_df = dataset_df[dataset_df['content'].str.strip() != ""]
    print("Rows with empty strings removed.")
except Exception as e:
    logging.error(
        f"Error removing rows with empty strings in 'content' column: {e}")

# Remove the paragraph_embedding_optimised from each data point in the dataset as we are going to create new embeddings with the new OpenAI embedding Model "text-embedding-3-large"
try:
    # dataset_df = dataset_df.dropna(subset=['content'])
    # print("\\nNumber of missing values in each column after removal:")
    # print(dataset_df.isnull().sum())
    print("Dropping column 'paragraph_embedding_optimised'...")
    dataset_df = dataset_df.drop(columns=['paragraph_embedding_optimised'])
    print("Column 'paragraph_embedding_optimised' dropped.")
except Exception as e:
    logging.error(f"Error dropping column: {e}")


# Generate embeddings for the 'content' column
try:
    print("Generating embeddings...")
    dataset_df["paragraph_embedding"] = dataset_df['content'].apply(
        get_embedding)
    print("Embeddings generated.")
except Exception as e:
    logging.error(f"Error generating embeddings: {e}")


# Convert DataFrame to dictionary
try:
    print("Converting DataFrame to dictionary...")
    documents = dataset_df.to_dict('records')
    print("DataFrame converted to dictionary.")
except Exception as e:
    logging.error(f"Error converting DataFrame to dictionary: {e}")
    print(f"Error converting DataFrame to dictionary: {e}")


# Save embeddings to a JSON file
try:
    print("Saving embeddings to JSON file...")
    with open("embeddings.json", "w") as f:
        json.dump(documents, f, indent=4)  # Indent for pretty formatting
    print("Embeddings saved to JSON file.")
except Exception as e:
    logging.error(f"Error saving embeddings to JSON file: {e}")
    print(f"Error saving embeddings to JSON file: {e}")

# Insert data into MongoDB
try:
    print("Inserting data into MongoDB...")
    collection.insert_many(documents)
    print("Data insertion into MongoDB completed.")
except Exception as e:
    logging.error(f"Error inserting data into MongoDB: {e}")
    print(f"Error inserting data into MongoDB: {e}")
