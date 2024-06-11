# Ask-Sai-Baba

Developing a website to make finding spiritual guidance easier for members of the religious community of Sathya Sai Baba.

## Setup

1. **Clone the Project**:
   Clone the project from [GitHub](https://github.com/abhiramkolluri/ask-sai-baba.git):
   ```bash
   git clone https://github.com/abhiramkolluri/ask-sai-baba.git
   ```

2. **Install Python and pip**:
   Make sure you have Python and pip installed. You can download Python from the [official Python website](https://www.python.org/). Pip usually comes with Python, but if it's not installed, you can install it separately.

3. **Navigate to the Project Directory**:
   Switch to the cloned project directory:
   ```bash
   cd ask-sai-baba
   ```

4. **Create and Activate a Virtual Environment**:
   ```bash
   python -m venv venv
   ```
   - On Windows:
     ```bash
     venv\Scripts\activate
     ```
   - On macOS and Linux:
     ```bash
     source venv/bin/activate
     ```

5. **Navigate to the Backend Directory**:
   ```bash
   cd backend
   ```

6. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

7. **Set Up OpenAI API Key**:
   Create a new file named `openai.ini` inside the backend directory. Add the following content:
   ```
   [OpenAI]
   api_key = YOUR_API_KEY
   ```
   Replace `YOUR_API_KEY` with your actual OpenAI API key (without quotation marks).

8. **Establish MongoDB Database Connection (FOR ADMINS ONLY)**:
   - Go to [MongoDB Atlas](https://cloud.mongodb.com/) and create a username and password.
   - Navigate to SECURITY > Database Access > ADD NEW DATABASE USER.
   - Under Authentication Method, ensure "Password" is highlighted. Enter the username and password, assign a Built-in Role, and click Add User.

9. **Configure MongoDB Credentials**:
   - Create a new file named `.env` inside the backend directory.
   - Add the MongoDB URI with the credentials provided by the ADMIN:
     ```
     MONGO_URI=mongodb+srv://<username>:<password>@ask-sai-vidya.rk7x0ch.mongodb.net/
     ```
     Replace `<username>` and `<password>` with the credentials provided.

10. **Run the Project**:
    - Export DEBUG mode to avoid restarting the project with every change:
      - On Windows:
        ```bash
        set FLASK_DEBUG=1
        ```
      - On macOS and Linux:
        ```bash
        export FLASK_DEBUG=1
        ```

    - Run the project:
      ```bash
      flask run
      ```

    This will initiate a process to create a new model on OpenAI using the training data found in `query_finetune.jsonl` located in the backend directory. The process should take about 10 minutes to complete. Once completed, the app will return a response to the default question "what is right to do?" on the terminal, indicating a successful setup.

## Test the Project on Postman
Donwload Postman from https://www.postman.com/downloads/

You can now test the project's endpoints using Postman. Make sure the backend server is running and use the appropriate endpoints to test the functionality of the project.

---

By following these steps, you should be able to set up and run the Ask-Sai-Baba project on your local machine successfully. If you encounter any issues, please consult the project's documentation or seek assistance from the project maintainers.