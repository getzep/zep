#!/usr/bin/env python3
import json
import sys
from collections import defaultdict
from pathlib import Path

def calculate_question_type_scores(jsonl_file):
    """Calculate scores by question_type from JSONL file."""
    question_type_stats = defaultdict(lambda: {'total': 0, 'correct': 0})
    
    with open(jsonl_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
                
            data = json.loads(line)
            question_type = data['question_type']
            grade = data['grade']
            
            question_type_stats[question_type]['total'] += 1
            if grade:
                question_type_stats[question_type]['correct'] += 1
    
    # Calculate scores
    scores = {}
    for question_type, stats in question_type_stats.items():
        score = stats['correct'] / stats['total'] if stats['total'] > 0 else 0
        scores[question_type] = {
            'score': score,
            'correct': stats['correct'],
            'total': stats['total']
        }
    
    return scores

def main():
    if len(sys.argv) != 2:
        print("Usage: python calculate_scores.py <jsonl_file>")
        sys.exit(1)
    
    jsonl_file = Path(sys.argv[1])
    if not jsonl_file.exists():
        print(f"Error: File {jsonl_file} not found")
        sys.exit(1)
    
    scores = calculate_question_type_scores(jsonl_file)
    
    print("Question Type Scores:")
    print("-" * 50)
    for question_type, data in sorted(scores.items()):
        print(f"{question_type:30} {data['score']:.3f} ({data['correct']}/{data['total']})")

if __name__ == "__main__":
    main()