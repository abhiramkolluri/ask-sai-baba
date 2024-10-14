import re
import uuid

import pandas as pd
from dotenv import load_dotenv
import os
from pymongo import MongoClient
import configparser
import json

from backend.utils import get_embedding

# Load environment variables and configurations
load_dotenv()
config = configparser.ConfigParser()
config.read('openai.ini')

# Initialize MongoDB client
client = MongoClient(os.getenv('MONGODB_URI'))
db = client.saibabasayings
collection = db.articles


# Load dataset from JSON file
def load_dataset(file_path):
    print(f"Loading dataset from {file_path}...")
    try:
        with open(file_path, 'r') as file:
            data = json.load(file)
            for item in data:
                # Ensure each document has an _id field
                if '_id' not in item:
                    # Generate a unique identifier if _id is missing
                    item['_id'] = re.sub(r'[^a-zA-Z0-9 ]', '', item['title']).replace(' ', '-') + str(uuid.uuid4())[-3:]

        # Write the modified _id back to data.json
        with open(file_path, 'w') as file:
            json.dump(data, file, indent=4)  # Use indent for pretty printing
        dataset_df = pd.DataFrame(data)
        print("Dataset loaded successfully.")
        return dataset_df
    except Exception as e:
        print(f"Error loading dataset: {e}")
        return None


# Clean the dataset
def clean_dataset(dataset_df):
    try:
        dataset_df = dataset_df.dropna(subset=['content'])
        dataset_df = dataset_df[dataset_df['content'].str.strip() != ""]
        print("Dataset cleaned.")
        return dataset_df
    except Exception as e:
        print(f"Error cleaning dataset: {e}")
        return dataset_df


# Generate embeddings using OpenAI
def generate_embeddings(dataset_df):
    try:
        print("Generating embeddings...")
        # Create a list to store the embeddings
        content_embeddings = []
        for index, row in dataset_df.iterrows():
            # Generate embedding for each document's content
            embedding = get_embedding(row['content'])
            content_embeddings.append(embedding)
            print(f"Completed embedding generation for document: {row.get('title')}")

        dataset_df["content_embedding"] = content_embeddings
        print("Embeddings generated for all documents.")
        return dataset_df
    except Exception as e:
        print(f"Error generating embeddings: {e}")
        return dataset_df


# Save the embeddings to JSON
def save_embeddings_to_json(output_file, documents):
    try:
        with open(output_file, 'w') as f:
            json.dump(documents, f, indent=4)
        print(f"Embeddings saved to {output_file}.")
    except Exception as e:
        print(f"Error saving embeddings: {e}")


# Insert data into MongoDB
def insert_data_into_mongo(documents):
    try:
        print("Inserting data into MongoDB...")
        collection.insert_many(documents)
        print("Data insertion into MongoDB completed.")
    except Exception as e:
        print(f"Error inserting data into MongoDB: {e}")


# Combine dataset processing and MongoDB insertion
def process_and_store_data(file_path, json_output_file):
    dataset_df = load_dataset(file_path)
    if dataset_df is not None:
        dataset_df = clean_dataset(dataset_df)
        dataset_df = generate_embeddings(dataset_df)
        documents = dataset_df.to_dict('records')
        save_embeddings_to_json(json_output_file, documents)
        insert_data_into_mongo(documents)


# Example usage
process_and_store_data('../Web scraper/data.json', 'embeddings.json')
