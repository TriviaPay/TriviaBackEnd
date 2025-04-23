// Serverless Node.js function
export default function handler(req, res) {
  res.status(200).json({
    status: 'online',
    message: 'TriviaPay API is working (JavaScript handler)!',
    path: req.url
  });
} 