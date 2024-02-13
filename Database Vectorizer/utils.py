def generateEmbeddings(model,text:str) -> list[float]:
    embeddings = model.encode(text)
    return embeddings.tolist()


def search(model,query,collection):
    pipeline = [
  {
    '$vectorSearch': {
      'index': 'vector_index', 
        'path': 'plot_embedding_hf', 
        'queryVector': generateEmbeddings(model,query),
        'numCandidates': 100, 
       'limit': 4
    }
  },
]

    result = collection.aggregate(pipeline=pipeline)

    print("-------------------------------showing results-----------------------------------")

    for i in result:
        print(f"Movie name: {i['title']}, \nMovie Plot : {i['plot']} \n")

def insertEmbedding(collection,model,newcollection,limit = 50):
    for document in collection.find().limit(limit):
        document['plot_embedding_hf'] = generateEmbeddings(model,document['plot'])
        newcollection.insert_one(document=document)



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