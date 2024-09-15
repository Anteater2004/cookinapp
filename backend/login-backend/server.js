// server.js
const express = require('express');
const fs = require('fs');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const cors = require('cors');
require('dotenv').config();

const app = express();
const PORT = process.env.PORT || 3001;
const JWT_SECRET = process.env.JWT_SECRET || 'supersecretkey';

app.use(cors());
app.use(express.json());

// Helper function to read users from JSON file
const readUsers = () => {
  try {
    // Read the users from users.json
    const data = fs.readFileSync('users.json', 'utf-8');
    return JSON.parse(data);
  } catch (err) {
    console.error('Error reading users:', err);
    return [];
  }
};

// Middleware to authenticate JWT token
const authenticateToken = (req, res, next) => {
  const authHeader = req.headers['authorization'];
  const token = authHeader && authHeader.split(' ')[1];
  
  if (!token) return res.sendStatus(401); // Unauthorized if no token

  jwt.verify(token, JWT_SECRET, (err, user) => {
    if (err) return res.sendStatus(403); // Forbidden if token is invalid
    req.user = user;
    next();
  });
};

// Login endpoint
app.post('/login', async (req, res) => {
  const { username, password } = req.body;

  const users = readUsers();
  const user = users.find((user) => user.username === username);

  if (!user) {
    return res.status(400).json({ message: 'Invalid username or password' });
  }

  const isMatch = await bcrypt.compare(password, user.password);
  if (!isMatch) {
    return res.status(400).json({ message: 'Invalid username or password' });
  }

  const token = jwt.sign({ username: user.username }, JWT_SECRET, { expiresIn: '1h' });
  res.json({ 
    token, 
    user: { 
      username: user.username, 
      email: user.email // Include the email in the response
    } 
  });
});

// Protected route example
app.get('/protected-endpoint', authenticateToken, (req, res) => {
  res.json({ message: 'This is a protected route', user: req.user });
});

// Endpoint to update user profile
app.put('/update-profile', authenticateToken, async (req, res) => {
  const { newPassword } = req.body;
  const { username } = req.user;

  const users = readUsers();
  const userIndex = users.findIndex((user) => user.username === username);

  if (userIndex === -1) {
    return res.status(404).json({ message: 'User not found' });
  }

  // Update the password if provided
  if (newPassword) {
    const hashedPassword = await bcrypt.hash(newPassword, 10);
    users[userIndex].password = hashedPassword;
  }

  // Save updated user data
  fs.writeFileSync('users.json', JSON.stringify(users, null, 2));
  res.json({ message: 'Profile updated successfully' });
});

// Start the server
app.listen(PORT, () => {
  console.log(`Server running on http://localhost:${PORT}`);
});
