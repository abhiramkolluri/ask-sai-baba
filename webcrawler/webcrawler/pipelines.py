# In your Scrapy project's pipelines.py

import scrapy
import csv
import os
from pymongo import MongoClient


class SaveScrapedItemsPipeline:
    def __init__(self):
        self.csv_file = 'scraped_items.csv'
        self.client = MongoClient('mongodb://localhost:27017/')
        self.db = self.client['your_database']
        self.collection = self.db['your_collection']

    def process_item(self, item, spider):
        self.save_as_csv(item)
        self.save_to_mongodb(item)
        return item

    def save_as_csv(self, item):
        # Check if CSV file exists, if not, create new with headers
        file_exists = os.path.isfile(self.csv_file)
        with open(self.csv_file, 'a', newline='', encoding='utf-8') as csvfile:
            fieldnames = list(item.keys())
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(item)

    def save_to_mongodb(self, item):
        self.collection.insert_one(dict(item))


class CsvPipeline:
    def open_spider(self, spider):
        self.file = open('output.csv', 'w', newline='', encoding='utf-8')
        self.writer = csv.DictWriter(
            self.file, fieldnames=['title', 'content'])
        self.writer.writeheader()

    def close_spider(self, spider):
        self.file.close()

    def process_item(self, item, spider):
        self.writer.writerow(item)
        return item


class PdfDownloaderPipeline:
    def process_item(self, item, spider):
        pdf_url = item.get('pdf_url')
        if pdf_url:
            request = scrapy.Request(pdf_url)
            dfd = spider.crawler.engine.download(request, spider)
            dfd.addBoth(self.return_item, item)
            return dfd
        else:
            return item

    def return_item(self, response, item):
        if response.status != 200:
            # Log the error if needed
            pass
        else:
            # Save the PDF file to disk
            filename = response.url.split('/')[-1]
            with open(os.path.join('pdf_files', filename), 'wb') as f:
                f.write(response.body)
        return item
