import os


def fine_tune_model(openai_client, jsonl_file='../backend/query_finetune.jsonl'):
    # Check if the JSONL file exists
    if not os.path.exists(jsonl_file):
        return "JSONL file not found.", None

    jsonl_last_modified = os.path.getmtime(jsonl_file)
    fine_tuning_required = False

    # List existing fine-tuning jobs
    list_models = openai_client.fine_tuning.jobs.list(limit=1)

    if not list_models or not list_models.data:
        fine_tuning_required = True
        print("No previous models found. Creating new model.")
    else:
        last_model = list_models.data[0]
        last_model_finished_at = last_model.finished_at

        # Check if fine-tuning is required based on JSONL modification time
        if last_model_finished_at is None or last_model_finished_at < jsonl_last_modified:
            fine_tuning_required = True
            print("JSONL file updated. Creating new model.")
        else:
            print("JSONL file hasn't been updated in a while. Using old model.")

    if fine_tuning_required:
        try:
            with open(jsonl_file, 'rb') as f:
                response = openai_client.files.create(file=f, purpose='fine-tune')
                file_id = response.id

            response = openai_client.fine_tuning.jobs.create(training_file=file_id, model="gpt-3.5-turbo-0125")
            job_id = response.id

            # Wait until the job finishes
            while True:
                job_status = openai_client.fine_tuning.jobs.retrieve(job_id)
                if job_status.status == 'succeeded':
                    fine_tuned_model_id = job_status.fine_tuned_model
                    print("Fine-tuned model created successfully.", fine_tuned_model_id)
                    with open('fine_tuned_model.txt', 'w') as model_file:
                        model_file.write(fine_tuned_model_id)
                    break
                elif job_status.status in ['failed', 'cancelled']:
                    print(f"Fine-tuning job {job_status.status}. Exiting loop.")
                    return "Fine-tuning job did not succeed.", None

            return fine_tuned_model_id
        except Exception as e:
            return f"Error during fine-tuning: {str(e)}"
    else:
        # Use the last fine-tuned model if fine-tuning is not required
        model_id = load_fine_tuned_model_id_from_file()
        return model_id


def load_fine_tuned_model_id_from_file():
    model_file_path = '../backend/fine_tuned_model.txt'
    if os.path.exists(model_file_path):
        with open(model_file_path, 'r') as f:
            return f.read().strip()
    else:
        return None
