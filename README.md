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
