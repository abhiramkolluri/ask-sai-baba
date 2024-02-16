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

    return list(collection.aggregate(pipeline=pipeline))
    
### inserting the embedding in the databse
# def insertEmbedding(collection,model,document):
#         document['doc_embedding'] = generateEmbeddings(model,document['Content'])
#         collection.insert_one(document=document)



