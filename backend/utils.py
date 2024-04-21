# def generateEmbeddings(model,text:str) -> list[float]:
#     embeddings = model.encode(text)
#     return embeddings.tolist()


def search(embedding,collection):
    ### aggregaition pipeline
    pipeline = [
  {
    '$vectorSearch': {
      'index': 'sathyasearch', 
        'path': 'content_embedding', 
        'queryVector': embedding,
        'numCandidates': 200,
       'limit': 4
    }
  },
]
    result = collection.aggregate(pipeline=pipeline)
    res = []
    for i in result:
        # print(f"Title: {i['title']}, \nContent : {i['Content']}, \nCollection : {i['collection:']},\nDate : {i['date:']}, \nDiscourse Number : {i['discourse_number:']} \n")
        res.append({
        'title': i['title'],
        'content': i['content'],
        # 'collection': i['collection:'],
        # 'date': i['date:'],
        # 'discourse_number': i['discourse_number:']
        })
    return res
    
### inserting the embedding in the databse
# def insertEmbedding(collection,model,document):
#         document['doc_embedding'] = generateEmbeddings(model,document['Content'])
#         collection.insert_one(document=document)



