export interface SlideMetadata {
  fileId: string;
  name: string;
  createdTime: string;
  modifiedTime: string;
  slideCount: number;
  thumbnailUrl?: string;
  owner: string;
  size?: number;
}

export interface SlideContent {
  slideIndex: number;
  title?: string;
  textContent: string;
  speakerNotes?: string;
  imageUrls?: string[];
}

export interface ProcessedSlide extends SlideContent {
  fileId: string;
  fileName: string;
  embedding?: number[];
  metadata: SlideMetadata;
}

export interface VectorSearchResult {
  slide: ProcessedSlide;
  similarity: number;
  relevanceScore: number;
}

export interface SearchQuery {
  query: string;
  limit?: number;
  threshold?: number;
  fileIds?: string[];
}

export interface AuthConfig {
  oauthPath: string;
  credentialsPath: string;
}

export interface MCPToolResult {
  content: Array<{
    type: "text";
    text: string;
  }>;
}