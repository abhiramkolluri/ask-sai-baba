import json
import configparser
from openai import OpenAI

config = configparser.ConfigParser()
# setting up openai
config.read('openai.ini')
openai_client = OpenAI(api_key=config['OpenAI']['api_key'])

# Function to load articles from data.json
def load_articles(file_path):
    with open(file_path, 'r') as file:
        return json.load(file)


# Function to generate questions based on the article content
def generate_questions(content):
    # Use OpenAI to generate questions based on the context of the article
    prompt = (
        f"Generate 1-2 conversational questions based on the following discourses and content taught by Sathya sai Baba or Swami:\n\n{content}\n\n"
        "Make the questions short and sound natural and human-like. Do not number the questions. Keep each question separate. "
        "Generate the questions in a manner such that a human is asking the question to Sathya Sai baba in order to progress in their spiritual journey. Please avoid words like \"your\" or \"teachings of Sathya Sai Baba\""
        "\"songs\" sung or mentioned by Sai Baba are \"bhajans\". Try to use Sanskrit words if possible at times"
    )

    response = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are an AI designed to generate natural-sounding questions from text."},
            {"role": "user", "content": prompt}
        ]
    )

    questions = response.choices[0].message.content.strip().split('\n')
    return questions


# Function to generate follow-up questions based on the previous question and response
def generate_follow_up_questions(question, response):
    prompt = (
        f"Based on this question: '{question}' and its response: '{response}', "
        "generate 3 natural follow-up questions that someone might ask to deepen their understanding. "
        "The questions should be related to Sathya Sai Baba's teachings and spiritual concepts mentioned in the response. "
        "Make the questions conversational and avoid using phrases like 'Could you explain' or 'What does Sai Baba say about'. "
        "Each question should explore a different aspect or go deeper into the topic."
    )

    response = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are an AI designed to generate natural follow-up questions about spiritual topics."},
            {"role": "user", "content": prompt}
        ]
    )

    follow_ups = response.choices[0].message.content.strip().split('\n')
    return follow_ups


# Function to get responses from ChatGPT
def get_responses(questions):
    responses = []
    follow_up_questions = []

    for question in questions:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system",
                 "content": "You are an AI assistant designed to help users find spiritual guidance from the teachings of Sathya Sai Baba.I do not have to mention \"According to Sai Baba\" for you to give me an answer. If a question is relevant to the teachings of Sathya Sai Baba, you can answer it. Please avoid using words like \"user\" or \"query\" in your response."},
                {"role": "user", "content": question}
            ]
        )
        response_text = response.choices[0].message.content
        responses.append(response_text)
        
        # Generate follow-up questions for each Q&A pair
        follow_ups = generate_follow_up_questions(question, response_text)
        follow_up_questions.append(follow_ups)

    return responses, follow_up_questions


# Function to save the questions and answers to a JSONL file
def save_to_jsonl(questions, responses, follow_up_questions, output_file):
    with open(output_file, 'a') as file:
        for question, response, follow_ups in zip(questions, responses, follow_up_questions):
            entry = {
                "messages": [
                    {"role": "system",
                     "content": "You are an AI assistant designed to help users find spiritual guidance from the teachings of Sathya Sai Baba.I do not have to mention \"According to Sai Baba\" for you to give me an answer. If a question is relevant to the teachings of Sathya Sai Baba, you can answer it. Please avoid using words like \"user\" or \"query\" in your response."},
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": response},
                    {"role": "system", "content": "Suggested follow-up questions:"},
                    {"role": "assistant", "content": "\n".join(follow_ups)}
                ]
            }
            file.write(json.dumps(entry) + "\n")


# Main execution
if __name__ == "__main__":
    #Take a copy of data.json, name if data1.json and take only few articles from data.json
    # see how the questions are being generated. If it looks good load up the full data.json file
    articles = load_articles('../Web scraper/data1.json')

    for article in articles:
        content = article['content']  # Extract content for each article
        questions = generate_questions(content)  # Generate questions for this article
        responses, follow_up_questions = get_responses(questions)  # Get responses for these questions
        save_to_jsonl(questions, responses, follow_up_questions, 'query_finetune_enhanced.jsonl')  # Save to JSONL
