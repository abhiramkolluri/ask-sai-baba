const axios = require("axios");
const { MongoClient } = require("mongodb");

const uri =
  "mongodb+srv://sathya:test123456@sathya.ll8whkx.mongodb.net/?retryWrites=true&w=majority";

// Database Name
const dbName = "your_database_name";

// Create a new MongoClient
const client = new MongoClient(uri, {
  useNewUrlParser: true,
  useUnifiedTopology: true,
});

// Connect to the MongoDB server
async function connectToMongo() {
  try {
    // Connect to the MongoDB server
    await client.connect();
    console.log("Connected to MongoDB");

    // Access the database
    const db = client.db(dbName);

    // Now you can work with the database
    // Example: Inserting a document
    await db.collection("your_collection_name").insertOne({ key: "value" });
    console.log("Document inserted successfully");
  } catch (error) {
    console.error("Error connecting to MongoDB:", error);
  } finally {
    // Close the connection
    await client.close();
    console.log("Disconnected from MongoDB");
  }
}

// Call the function to connect
connectToMongo();

async function getEmbedding(query) {
  const url = "https://api.openai.com/v1/embeddings";

  let response = await axios.post(
    url,
    {
      input: query,
      model: "text-embedding-ada-002",
    },
    {
      haeders: {
        Authorization: `Bearer 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`,
        "Content-Type": "application/json",
      },
    }
  );

  if (response === 200) {
    return response.data.data[0].embedding;
  } else {
    throw new Error(
      `Failed to get embedding. Status code : ${response.status}`
    );
  }
}
