#!/usr/bin/env python3
"""
Analyze the LongMemEval dataset and output session statistics.

This script analyzes each session in the dataset and outputs:
- Session identifiers (entry_id, session_id)
- Question type
- Total messages
- Number of user and assistant messages
- Role patterns
"""

import json
import csv
from collections import Counter
from datetime import datetime

def analyze_dataset(input_file: str, output_file: str):
    """
    Analyze the LongMemEval dataset and output session statistics.
    
    Args:
        input_file: Path to the JSON dataset file
        output_file: Path to the output CSV file
    """
    print(f"Loading dataset from {input_file}...")
    
    with open(input_file, 'r') as f:
        data = json.load(f)
    
    print(f"Loaded {len(data)} entries from dataset")
    
    # Prepare output data
    session_stats = []
    overall_stats = {
        'total_entries': len(data),
        'total_sessions': 0,
        'total_messages': 0,
        'sessions_by_question_type': Counter(),
        'role_patterns': Counter(),
        'consecutive_same_roles': 0,
        'user_only_sessions': 0,
        'assistant_only_sessions': 0,
        'mixed_sessions': 0
    }
    
    print("Analyzing sessions...")
    
    for entry_idx, entry in enumerate(data):
        question_type = entry['question_type']
        question_id = entry.get('question_id', f'entry_{entry_idx}')
        sessions = entry['haystack_sessions']
        
        overall_stats['sessions_by_question_type'][question_type] += len(sessions)
        
        for session_idx, session in enumerate(sessions):
            if len(session) == 0:
                continue  # Skip empty sessions
                
            overall_stats['total_sessions'] += 1
            overall_stats['total_messages'] += len(session)
            
            # Extract roles
            roles = [msg['role'] for msg in session]
            unique_roles = set(roles)
            
            # Count messages by role
            user_count = roles.count('user')
            assistant_count = roles.count('assistant')
            
            # Create role pattern string
            role_pattern = '->'.join(roles)
            overall_stats['role_patterns'][role_pattern] += 1
            
            # Check for consecutive same roles
            has_consecutive_same = any(
                roles[i] == roles[i-1] for i in range(1, len(roles))
            )
            if has_consecutive_same:
                overall_stats['consecutive_same_roles'] += 1
            
            # Categorize session type
            session_type = "mixed"
            if unique_roles == {'user'}:
                overall_stats['user_only_sessions'] += 1
                session_type = "user_only"
            elif unique_roles == {'assistant'}:
                overall_stats['assistant_only_sessions'] += 1
                session_type = "assistant_only"
            else:
                overall_stats['mixed_sessions'] += 1
            
            # Detect anomalies
            anomalies = []
            if has_consecutive_same:
                consecutive_count = sum(
                    1 for i in range(1, len(roles)) if roles[i] == roles[i-1]
                )
                anomalies.append(f"consecutive_same_roles({consecutive_count})")
            
            if len(unique_roles) == 1 and len(session) > 1:
                anomalies.append(f"single_role_only({list(unique_roles)[0]})")
            
            if not roles:
                anomalies.append("empty_session")
            
            # Create session record
            session_record = {
                'entry_id': entry_idx,
                'session_id': session_idx,
                'question_id': question_id,
                'question_type': question_type,
                'total_messages': len(session),
                'user_messages': user_count,
                'assistant_messages': assistant_count,
                'session_type': session_type,
                'role_pattern': role_pattern,
                'has_consecutive_same_roles': has_consecutive_same,
                'unique_roles': sorted(list(unique_roles)),
                'anomalies': ';'.join(anomalies) if anomalies else 'none'
            }
            
            session_stats.append(session_record)
        
        # Progress indicator
        if (entry_idx + 1) % 100 == 0:
            print(f"Processed {entry_idx + 1}/{len(data)} entries...")
    
    print(f"Analysis complete. Found {len(session_stats)} non-empty sessions")
    
    # Write detailed session data to CSV
    print(f"Writing detailed session data to {output_file}...")
    
    fieldnames = [
        'entry_id', 'session_id', 'question_id', 'question_type',
        'total_messages', 'user_messages', 'assistant_messages',
        'session_type', 'role_pattern', 'has_consecutive_same_roles',
        'unique_roles', 'anomalies'
    ]
    
    with open(output_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(session_stats)
    
    # Write summary statistics
    summary_file = output_file.replace('.csv', '_summary.txt')
    print(f"Writing summary statistics to {summary_file}...")
    
    with open(summary_file, 'w') as f:
        f.write("LongMemEval Dataset Session Analysis Summary\\n")
        f.write("=" * 50 + "\\n")
        f.write(f"Analysis performed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\\n\\n")
        
        f.write("Overall Statistics:\\n")
        f.write(f"  Total entries: {overall_stats['total_entries']}\\n")
        f.write(f"  Total non-empty sessions: {overall_stats['total_sessions']}\\n")
        f.write(f"  Total messages: {overall_stats['total_messages']}\\n")
        f.write(f"  Average messages per session: {overall_stats['total_messages'] / overall_stats['total_sessions']:.2f}\\n\\n")
        
        f.write("Session Types:\\n")
        f.write(f"  Mixed user/assistant sessions: {overall_stats['mixed_sessions']}\\n")
        f.write(f"  User-only sessions: {overall_stats['user_only_sessions']}\\n")
        f.write(f"  Assistant-only sessions: {overall_stats['assistant_only_sessions']}\\n\\n")
        
        f.write("Anomalies:\\n")
        f.write(f"  Sessions with consecutive same roles: {overall_stats['consecutive_same_roles']}\\n\\n")
        
        f.write("Sessions by Question Type:\\n")
        for qtype, count in overall_stats['sessions_by_question_type'].most_common():
            f.write(f"  {qtype}: {count}\\n")
        
        f.write("\\nMost Common Role Patterns (top 20):\\n")
        for pattern, count in overall_stats['role_patterns'].most_common(20):
            if len(pattern) > 100:
                pattern = pattern[:97] + "..."
            f.write(f"  {pattern}: {count}\\n")
        
        f.write("\\nAnomalous Sessions (consecutive same roles):\\n")
        anomalous_sessions = [s for s in session_stats if s['has_consecutive_same_roles']]
        for session in anomalous_sessions[:10]:  # Show first 10
            f.write(f"  Entry {session['entry_id']}, Session {session['session_id']} ")
            f.write(f"({session['question_type']}): {session['role_pattern']}\\n")
        
        if len(anomalous_sessions) > 10:
            f.write(f"  ... and {len(anomalous_sessions) - 10} more\\n")
    
    print("\\nAnalysis Summary:")
    print(f"  Total sessions analyzed: {len(session_stats)}")
    print(f"  Sessions with consecutive same roles: {overall_stats['consecutive_same_roles']}")
    print(f"  User-only sessions: {overall_stats['user_only_sessions']}")
    print(f"  Assistant-only sessions: {overall_stats['assistant_only_sessions']}")
    print(f"\\nOutput files:")
    print(f"  Detailed data: {output_file}")
    print(f"  Summary: {summary_file}")

def main():
    """Main function."""
    input_file = "data/longmemeval_s.json"
    output_file = "session_analysis.csv"
    
    print("LongMemEval Dataset Session Analyzer")
    print("=" * 40)
    
    try:
        analyze_dataset(input_file, output_file)
        print("\\nAnalysis completed successfully!")
        
    except FileNotFoundError as e:
        print(f"Error: Could not find input file: {e}")
        print("Make sure 'data/longmemeval_s.json' exists in the current directory")
        
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON format: {e}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()