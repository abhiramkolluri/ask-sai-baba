def generateEmbeddings(model,text:str) -> list[float]:
    embeddings = model.encode(text)
    return embeddings.tolist()


def search(embedding,collection):
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
    # print(list(result))
    # print("-------------------------------------------------showing results--------------------------------------------------------")
    res = []
    for i in result:
        # print(f"Title: {i['title']}, \nContent : {i['Content']}, \nCollection : {i['collection:']},\nDate : {i['date:']}, \nDiscourse Number : {i['discourse_number:']} \n")
        res.append({
        'title': i['title'],
        'content': i['Content'],
        'collection': i['collection:'],
        'date': i['date:'],
        'discourse_number': i['discourse_number:']
        })
    return res

def insertEmbedding(collection,model,document):
        document['doc_embedding'] = generateEmbeddings(model,document['Content'])
        collection.insert_one(document=document)



# def generateEmbeddings(text:str) -> list[float]:
#     embeddings = model.encode(text)
#     return embeddings.tolist()

# for document in collection.find().limit(50):
#     document['plot_embedding_hf'] = generateEmbeddings(document['plot'])
#     testCollection.insert_one(document=document)

# query = "I want a movie about a war"

# pipeline = [
#   {
#     '$vectorSearch': {
#       'index': 'vector_index', 
#         'path': 'plot_embedding_hf', 
#         'queryVector': generateEmbeddings(query),
#         'numCandidates': 100, 
#        'limit': 4
#     }
#   },
# ]

# result = testCollection.aggregate(pipeline=pipeline)

# print("showing results")

# for i in result:
#     print(f"Movie name: {i['title']}, \nMovie Plot : {i['plot']} \n")