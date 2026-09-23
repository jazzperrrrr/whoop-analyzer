import { MockApiClient } from './client';

// Fictional, static presentation data only. The frozen MockApiClient stays independent.
// Contract validation checks this JSON separately; never loads a backend or personal data.
export class VisualDemoApiClient extends MockApiClient {
  constructor() { super('visual-demo'); }
}
