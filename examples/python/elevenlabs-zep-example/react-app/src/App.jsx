import { useState, useCallback, useEffect, useRef } from 'react';
import { useConversation } from '@11labs/react';

// Configuration - set these in your .env file
const AGENT_ID = import.meta.env.VITE_ELEVENLABS_AGENT_ID;
const PROXY_URL = import.meta.env.VITE_PROXY_URL || 'http://localhost:8080';

export default function App() {
  const [userUuid, setUserUuid] = useState(() => localStorage.getItem('zep_user_uuid'));
  const [conversationId, setConversationId] = useState(null);
  const [status, setStatus] = useState('disconnected');
  const [messages, setMessages] = useState([]);
  const [error, setError] = useState(null);
  const [isCreatingUser, setIsCreatingUser] = useState(false);
  const userCreationRef = useRef(null);

  const createZepUser = useCallback(async () => {
    if (userCreationRef.current) {
      return userCreationRef.current;
    }
    const creation = (async () => {
      setIsCreatingUser(true);
      try {
        const response = await fetch(`${PROXY_URL}/zep/users`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        if (!response.ok) {
          throw new Error(`Could not create Zep user: HTTP ${response.status}`);
        }
        const result = await response.json();
        if (!result.user_uuid) {
          throw new Error('The proxy did not return a Zep user UUID');
        }
        localStorage.setItem('zep_user_uuid', result.user_uuid);
        setUserUuid(result.user_uuid);
        return result.user_uuid;
      } finally {
        setIsCreatingUser(false);
      }
    })();
    userCreationRef.current = creation;
    try {
      return await creation;
    } finally {
      userCreationRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!userUuid) {
      createZepUser().catch((err) => setError(err.message || 'Could not create Zep user'));
    }
  }, [createZepUser, userUuid]);

  const resetUserUuid = useCallback(async () => {
    setError(null);
    setMessages([]);
    setConversationId(null);
    try {
      await createZepUser();
    } catch (err) {
      setError(err.message || 'Could not create Zep user');
    }
  }, [createZepUser]);

  // PERFORMANCE OPTIMIZATION: Warm the Zep cache when the user arrives on the page.
  // This moves the user's data into Zep's "hot" cache before they start speaking,
  // making the first context retrieval faster. Zep has a multi-tier architecture
  // where inactive user data moves to slower storage after a few hours.
  // Call this whenever the user may soon speak with the agent.
  useEffect(() => {
    if (!userUuid) {
      return;
    }
    const warmUserCache = async () => {
      try {
        console.log(`Warming Zep cache for user: ${userUuid}`);
        const response = await fetch(`${PROXY_URL}/warm-user-cache`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ user_id: userUuid }),
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
  }, [userUuid]);

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

      if (!userUuid) {
        throw new Error('Wait for the Zep user to be created before starting a session');
      }

      // Request microphone permission
      await navigator.mediaDevices.getUserMedia({ audio: true });

      const threadResponse = await fetch(`${PROXY_URL}/zep/threads`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_uuid: userUuid }),
      });
      if (!threadResponse.ok) {
        throw new Error(`Could not create Zep thread: HTTP ${threadResponse.status}`);
      }
      const { thread_uuid: threadUuid } = await threadResponse.json();
      if (!threadUuid) {
        throw new Error('The proxy did not return a Zep thread UUID');
      }

      const elevenLabsConvId = await conversation.startSession({
        agentId: AGENT_ID,
        customLlmExtraBody: {
          user_id: userUuid,
          conversation_id: threadUuid,
        },
      });

      console.log('ElevenLabs Conversation ID:', elevenLabsConvId);
      console.log('Zep Thread UUID:', threadUuid);
      setConversationId(threadUuid);

    } catch (err) {
      console.error('Failed to start conversation:', err);
      setError(err.message || 'Failed to start conversation');
      setStatus('error');
    }
  }, [conversation, userUuid]);

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
          <strong>Zep User UUID:</strong> {userUuid || (isCreatingUser ? 'Creating...' : 'Unavailable')}
          <button
            onClick={resetUserUuid}
            style={styles.resetButton}
            disabled={status === 'connected' || status === 'connecting' || isCreatingUser}
          >
            🔄 New User
          </button>
        </p>
        <p><strong>Zep Thread UUID:</strong> {conversationId || 'Not started'}</p>
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
            disabled={!userUuid || isCreatingUser}
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
    boxShadow: '0 0 0 0 rgba(76, 175, 80, 0.7)',
    // Note: For a pulsing animation, add CSS keyframes to your global stylesheet:
    // @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(76, 175, 80, 0.7); } 70% { box-shadow: 0 0 0 10px rgba(76, 175, 80, 0); } 100% { box-shadow: 0 0 0 0 rgba(76, 175, 80, 0); } }
    // Then uncomment: animation: 'pulse 1.5s ease-in-out infinite',
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
