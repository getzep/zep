# Zep Graph Visualization

A Next.js application for visualizing graph data using D3.js, built to work with [Zep](https://help.getzep.com). This is designed to serve as a reference implementation of Graph Visualization for Zep users in their applications.

Zep is a memory layer for AI assistants and agents that continuously learns from user interactions and changing business data. Zep ensures that your Agent has a complete and holistic view of the user, enabling you to build more personalized and accurate user experiences.

## Features

- Interactive graph visualization of knowledge graphs built with Zep
- Force-directed layout with D3.js
- Zoom and pan functionality
- Node and edge highlighting
- Node and edge inspection
- Dark and light mode support
- Custom node colors based on entity types
- Edge labeling

## Technology Stack

- [Next.js 15](https://nextjs.org/) with App Router
- [React 19](https://react.dev/)
- [D3.js](https://d3js.org/) for graph visualization
- [Tailwind CSS](https://tailwindcss.com/) for styling
- [Shadcn UI](https://ui.shadcn.com/) for UI components
- [Zep Cloud SDK](https://help.getzep.com/sdks/)

## Getting Started

### Prerequisites

- Node.js 18+ installed
- A Zep API key (if connecting to Zep Cloud)

### Installation

1. Clone the repository:

```bash
git clone https://github.com/getzep/zep-graph-visualization.git
cd zep-graph-visualization
```

2. Install dependencies:

```bash
npm install
# or
yarn install
# or
pnpm install
```

3. Set up environment variables:

Create a `.env.local` file in the root directory with the following variables:

```
ZEP_API_KEY=your_zep_api_key
```

### Running the Development Server

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the application.

## Usage

The application provides an interactive graph visualization of knowledge triplets from Zep. You can:

- Click on nodes to see their details
- Click on edges to see relationship information
- Zoom in/out using the mouse wheel
- Pan the graph by dragging
- Toggle between dark and light modes

## License

[MIT](LICENSE)
