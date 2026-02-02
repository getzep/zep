import { useState, useCallback, useEffect } from 'react';
import { useConversation } from '@11labs/react';
import { v4 as uuidv4 } from 'uuid';

// Configuration - set these in your .env file
const AGENT_ID = import.meta.env.VITE_ELEVENLABS_AGENT_ID;
const PROXY_URL = import.meta.env.VITE_PROXY_URL || 'http://localhost:8080';

// Generate or retrieve persistent user ID
function getUserId() {
  let userId = localStorage.getItem('zep_user_id');
  if (!userId) {
    userId = `user-${uuidv4().slice(0, 8)}`;
    localStorage.setItem('zep_user_id', userId);
  }
  return userId;
}

export default function App() {
  const [userId, setUserId] = useState(getUserId);
  const [conversationId, setConversationId] = useState(null);
  const [status, setStatus] = useState('disconnected');
  const [messages, setMessages] = useState([]);
  const [error, setError] = useState(null);

  // Reset to a new user ID
  const resetUserId = useCallback(() => {
    const newUserId = `user-${uuidv4().slice(0, 8)}`;
    localStorage.setItem('zep_user_id', newUserId);
    setUserId(newUserId);
    setMessages([]);
    setConversationId(null);
  }, []);

  // PERFORMANCE OPTIMIZATION: Warm the Zep cache when the user arrives on the page.
  // This moves the user's data into Zep's "hot" cache before they start speaking,
  // making the first context retrieval faster. Zep has a multi-tier architecture
  // where inactive user data moves to slower storage after a few hours.
  // Call this whenever the user may soon speak with the agent.
  useEffect(() => {
    const warmUserCache = async () => {
      try {
        console.log(`Warming Zep cache for user: ${userId}`);
        const response = await fetch(`${PROXY_URL}/warm-user-cache`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ user_id: userId }),
        });

        if (response.ok) {
          const result = await response.json();
          console.log('Cache warm result:', result);
        } else {
          console.warn('Failed to warm cache:', response.status);
        }
      } catch (err) {
        // Non-critical error - the conversation will still work, just potentially slower
        console.warn('Could not warm user cache (proxy may not be running):', err.message);
      }
    };

    // Warm the cache when component mounts or when userId changes
    warmUserCache();
  }, [userId]);

  // Initialize the ElevenLabs conversation hook
  const conversation = useConversation({
    onConnect: () => {
      console.log('Connected to ElevenLabs');
      setStatus('connected');
      setError(null);
    },
    onDisconnect: () => {
      console.log('Disconnected from ElevenLabs');
      setStatus('disconnected');
    },
    onMessage: (message) => {
      console.log('Message:', message);
      setMessages(prev => [...prev, message]);
    },
    onError: (err) => {
      console.error('Conversation error:', err);
      setError(err.message || 'An error occurred');
      setStatus('error');
    },
  });

  // Start the conversation session
  const startConversation = useCallback(async () => {
    try {
      setStatus('connecting');
      setError(null);
      setMessages([]);

      // Request microphone permission
      await navigator.mediaDevices.getUserMedia({ audio: true });

      // Pre-generate a conversation_id for Zep thread tracking
      // We generate this ourselves since ElevenLabs' conversation_id isn't available
      // until after startSession() returns
      const zepConversationId = `conv-${uuidv4().slice(0, 12)}`;

      // Start the session with user_id and conversation_id in customLlmExtraBody
      // This gets forwarded as "elevenlabs_extra_body" to our proxy
      const elevenLabsConvId = await conversation.startSession({
        agentId: AGENT_ID,
        customLlmExtraBody: {
          user_id: userId,
          conversation_id: zepConversationId,  // Our pre-generated ID for Zep
        },
      });

      console.log('ElevenLabs Conversation ID:', elevenLabsConvId);
      console.log('Zep Conversation ID:', zepConversationId);
      setConversationId(zepConversationId);  // Display the Zep conversation ID

    } catch (err) {
      console.error('Failed to start conversation:', err);
      setError(err.message || 'Failed to start conversation');
      setStatus('error');
    }
  }, [conversation, userId]);

  // End the conversation session
  const endConversation = useCallback(async () => {
    try {
      await conversation.endSession();
      setConversationId(null);
    } catch (err) {
      console.error('Failed to end conversation:', err);
    }
  }, [conversation]);

  return (
    <div style={styles.container}>
      <h1 style={styles.title}>ElevenLabs + Zep Memory Demo</h1>

      <div style={styles.infoBox}>
        <p>
          <strong>User ID:</strong> {userId}
          <button
            onClick={resetUserId}
            style={styles.resetButton}
            disabled={status === 'connected' || status === 'connecting'}
          >
            🔄 New User
          </button>
        </p>
        <p><strong>Conversation ID:</strong> {conversationId || 'Not started'}</p>
        <p><strong>Status:</strong> <span style={styles.status[status]}>{status}</span></p>
      </div>

      {error && (
        <div style={styles.errorBox}>
          <strong>Error:</strong> {error}
        </div>
      )}

      <div style={styles.buttonGroup}>
        {status === 'disconnected' || status === 'error' ? (
          <button
            onClick={startConversation}
            style={styles.startButton}
          >
            🎤 Start Conversation
          </button>
        ) : status === 'connecting' ? (
          <button style={styles.disabledButton} disabled>
            Connecting...
          </button>
        ) : (
          <button onClick={endConversation} style={styles.endButton}>
            🛑 End Conversation
          </button>
        )}
      </div>

      {status === 'connected' && (
        <div style={styles.activeIndicator}>
          <div style={styles.pulse}></div>
          <span>Listening... Speak to the agent</span>
        </div>
      )}

      {messages.length > 0 && (
        <div style={styles.messagesContainer}>
          <h3>Conversation Log:</h3>
          <div style={styles.messagesList}>
            {messages.map((msg, index) => (
              <div key={index} style={styles.message}>
                <pre>{JSON.stringify(msg, null, 2)}</pre>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={styles.instructions}>
        <h3>Setup Instructions:</h3>
        <ol>
          <li>Update <code>AGENT_ID</code> in this file with your ElevenLabs agent ID</li>
          <li>Make sure your agent is configured with a Custom LLM pointing to your proxy</li>
          <li>Enable "Custom LLM extra body" in your agent's security settings</li>
          <li>Run the proxy server: <code>cd elevenlabs-zep-proxy && python proxy_server.py</code></li>
          <li>Expose the proxy with ngrok: <code>ngrok http 8080</code></li>
          <li>Update your agent's Custom LLM URL to the ngrok URL</li>
        </ol>
      </div>
    </div>
  );
}

const styles = {
  container: {
    maxWidth: '800px',
    margin: '0 auto',
    padding: '20px',
  },
  title: {
    textAlign: 'center',
    color: '#333',
  },
  infoBox: {
    background: '#fff',
    padding: '15px',
    borderRadius: '8px',
    marginBottom: '20px',
    boxShadow: '0 2px 4px rgba(0,0,0,0.1)',
  },
  errorBox: {
    background: '#fee',
    border: '1px solid #fcc',
    color: '#c00',
    padding: '15px',
    borderRadius: '8px',
    marginBottom: '20px',
  },
  buttonGroup: {
    display: 'flex',
    justifyContent: 'center',
    marginBottom: '20px',
  },
  startButton: {
    background: '#4CAF50',
    color: 'white',
    border: 'none',
    padding: '15px 30px',
    fontSize: '18px',
    borderRadius: '8px',
    cursor: 'pointer',
  },
  resetButton: {
    background: '#2196F3',
    color: 'white',
    border: 'none',
    padding: '5px 12px',
    fontSize: '12px',
    borderRadius: '4px',
    cursor: 'pointer',
    marginLeft: '10px',
  },
  endButton: {
    background: '#f44336',
    color: 'white',
    border: 'none',
    padding: '15px 30px',
    fontSize: '18px',
    borderRadius: '8px',
    cursor: 'pointer',
  },
  disabledButton: {
    background: '#ccc',
    color: '#666',
    border: 'none',
    padding: '15px 30px',
    fontSize: '18px',
    borderRadius: '8px',
    cursor: 'not-allowed',
  },
  activeIndicator: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '10px',
    padding: '20px',
    background: '#e8f5e9',
    borderRadius: '8px',
    marginBottom: '20px',
  },
  pulse: {
    width: '20px',
    height: '20px',
    background: '#4CAF50',
    borderRadius: '50%',
    animation: 'pulse 1.5s ease-in-out infinite',
  },
  messagesContainer: {
    background: '#fff',
    padding: '15px',
    borderRadius: '8px',
    marginBottom: '20px',
    boxShadow: '0 2px 4px rgba(0,0,0,0.1)',
  },
  messagesList: {
    maxHeight: '300px',
    overflow: 'auto',
  },
  message: {
    background: '#f5f5f5',
    padding: '10px',
    margin: '5px 0',
    borderRadius: '4px',
    fontSize: '12px',
  },
  instructions: {
    background: '#fff3cd',
    padding: '15px',
    borderRadius: '8px',
    marginTop: '20px',
  },
  status: {
    disconnected: { color: '#999' },
    connecting: { color: '#ff9800' },
    connected: { color: '#4CAF50' },
    error: { color: '#f44336' },
  },
};
