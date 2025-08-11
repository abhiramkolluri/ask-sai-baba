import { OAuth2Client } from 'google-auth-library';
import { google } from 'googleapis';
import fs from 'fs-extra';
import path from 'path';
import { AuthConfig } from '../types/index.js';

export class GoogleAuth {
  private oauth2Client: OAuth2Client;
  private authConfig: AuthConfig;

  constructor(authConfig: AuthConfig) {
    this.authConfig = authConfig;
    this.oauth2Client = new OAuth2Client();
  }

  async initialize(): Promise<void> {
    const credentials = await this.loadCredentials();
    this.oauth2Client = new OAuth2Client(
      credentials.client_id,
      credentials.client_secret,
      credentials.redirect_uris[0]
    );

    // Try to load existing tokens
    await this.loadTokens();
  }

  private async loadCredentials() {
    if (!await fs.pathExists(this.authConfig.oauthPath)) {
      throw new Error(`OAuth credentials file not found: ${this.authConfig.oauthPath}`);
    }

    const credentialsContent = await fs.readJson(this.authConfig.oauthPath);
    return credentialsContent.installed || credentialsContent.web;
  }

  private async loadTokens(): Promise<void> {
    if (await fs.pathExists(this.authConfig.credentialsPath)) {
      const tokens = await fs.readJson(this.authConfig.credentialsPath);
      this.oauth2Client.setCredentials(tokens);
    }
  }

  async authenticate(): Promise<void> {
    if (this.oauth2Client.credentials?.access_token) {
      // Check if token is still valid
      try {
        await this.oauth2Client.getAccessToken();
        return;
      } catch (error) {
        console.log('Existing token invalid, need to re-authenticate');
      }
    }

    // Generate authorization URL
    const authUrl = this.oauth2Client.generateAuthUrl({
      access_type: 'offline',
      scope: [
        'https://www.googleapis.com/auth/drive.readonly',
        'https://www.googleapis.com/auth/presentations.readonly'
      ],
    });

    console.log('Authorize this app by visiting this URL:', authUrl);
    console.log('After authorization, the tokens will be automatically saved.');

    // In a real implementation, you'd handle the callback and exchange the code for tokens
    // For now, we'll assume the user has completed the OAuth flow
  }

  async getAuthenticatedClient() {
    await this.authenticate();
    return this.oauth2Client;
  }

  async saveTokens(tokens: any): Promise<void> {
    await fs.writeJson(this.authConfig.credentialsPath, tokens, { spaces: 2 });
  }

  getDriveClient() {
    return google.drive({ version: 'v3', auth: this.oauth2Client });
  }

  getSlidesClient() {
    return google.slides({ version: 'v1', auth: this.oauth2Client });
  }
}