import os
import pickle
import re
import numpy as np
import tiktoken
import json
import logging
from azure.storage.blob import BlobServiceClient, BlobClient
from azure.storage.blob import ContainerClient, __version__
from azure.storage.blob import generate_blob_sas, BlobSasPermissions


from utils import language
from utils import storage
from utils import redis_helpers
from utils import openai_helpers
from utils.kb_doc import KB_Doc


OVERLAP_TEXT = int(os.environ["OVERLAP_TEXT"])
NUM_TOP_MATCHES = int(os.environ['NUM_TOP_MATCHES'])
CHOSEN_EMB_MODEL   = os.environ['CHOSEN_EMB_MODEL']
CHOSEN_QUERY_EMB_MODEL   = os.environ['CHOSEN_QUERY_EMB_MODEL']
CHOSEN_COMP_MODEL   = os.environ['CHOSEN_COMP_MODEL']
MAX_SEARCH_TOKENS  = int(os.environ.get("MAX_SEARCH_TOKENS"))





def generate_embeddings(full_kbd_doc, embedding_model, max_emb_tokens, previous_max_tokens = 0, text_suffix = '',  gen_emb=True):
    
    emb_documents = []

    json_object = full_kbd_doc.get_dict()

    logging.info(f"Starting to generate embeddings with {embedding_model} and {max_emb_tokens} tokens")

    try:
        timestamp = json_object['timestamp'][0]
    except:
        timestamp = "1/1/1970 00:00:00 AM"

    doc_id = json_object['id']
    doc_text = json_object['text']
    doc_url = storage.create_sas(json_object.get('doc_url', "https://microsoft.com"))
    filename = os.path.basename(doc_url)
    access = 'public'

    if filename.startswith('PRIVATE_'):
        access = 'private'

    enc = openai_helpers.get_encoder(embedding_model)
    tokens = enc.encode(doc_text)
    lang = language.detect_content_language(doc_text[:500])

    print("Comparing lengths", len(tokens) , previous_max_tokens-OVERLAP_TEXT)

    if (len(tokens) < previous_max_tokens-OVERLAP_TEXT) and (previous_max_tokens > 0):
        print("Skipping generating embeddings as it is optional for this text")
        return emb_documents


    suff = 0 
    for chunk in chunked_words(tokens, chunk_length=max_emb_tokens-OVERLAP_TEXT):
        decoded_chunk = enc.decode(chunk)
        translated_chunk = decoded_chunk
        if lang != 'en': translated_chunk = language.translate(decoded_chunk, lang)
       
        if gen_emb:
            embedding = openai_helpers.get_openai_embedding(translated_chunk, embedding_model)
        else:
            embedding = ''


        chunk_kbd_doc = KB_Doc()
        chunk_kbd_doc.load({
                            'id':f"{doc_id}_{text_suffix}_{suff}", 
                            'text_en': translated_chunk, 
                            'text': decoded_chunk, 
                            'doc_url': doc_url, 
                            'timestamp': timestamp, 
                            'item_vector': embedding,
                            'orig_lang': lang,
                            'access': access
                        })

        emb_documents.append(chunk_kbd_doc.get_dict())
        suff += 1

        if suff % 100 == 0:
            print (f'Processed: {suff} embeddings for document {filename}')
            logging.info (f'Processed: {suff} embeddings for document {filename}')


    print(f"This doc generated {suff} chunks")
    logging.info(f"This doc generated {suff} chunks")

    return emb_documents



def generate_embeddings_from_json_docs(json_folder, embedding_model, max_emb_tokens, text_suffix='M', limit = -1):
    
    emb_documents = []

    counter = 0
    for item in os.listdir(json_folder):
        if (limit != -1 ) and (counter >= limit): break
        path = os.path.join(json_folder, item)

        with open(path, 'r') as openfile:
            json_object = json.load(openfile)
        
        doc_embs = generate_embeddings(json_object, embedding_model, max_emb_tokens = max_emb_tokens, text_suffix = text_suffix)
        emb_documents += doc_embs
        counter += 1

        print(f"Now processing {path}, generated {len(doc_embs)} chunks")

    return emb_documents



def save_embedding_docs_to_pkl(emb_documents, emb_filename):
    with open(emb_filename, 'wb') as pickle_out:
        pickle.dump(emb_documents, pickle_out)


def load_embedding_docs_from_pkl(emb_filename):
    with open(emb_filename, 'rb') as pickle_in:
        emb_documents = pickle.load(pickle_in)

    return emb_documents  


def load_embedding_docs_in_redis(emb_documents, emb_filename = '', document_name = ''):

    if (emb_documents is None) and (emb_filename != ''):
        emb_documents = load_embedding_docs_from_pkl(emb_filename)

    redis_conn = redis_helpers.get_new_conn()

    print(f"Loading {len(emb_documents)} embeddings into Redis")
    logging.info(f"Loading {len(emb_documents)} embeddings into Redis")

    counter = 0
    loaded = 0

    for e in emb_documents:
        loaded += redis_helpers.redis_upsert_embedding(redis_conn, e)

        counter +=1
        if counter % 200 == 0:
            print (f'Processed: {counter} of {len(emb_documents)} for document {document_name}')
            logging.info (f'Processed: {counter} of {len(emb_documents)} for document {document_name}')
    
    print (f'Processed: {counter} of {len(emb_documents)} for document {document_name}')

    return loaded


def chunked_words(tokens, chunk_length, overlap=OVERLAP_TEXT):
    num_slices = len(tokens) // chunk_length + (len(tokens) % chunk_length > 0)
    chunks_iterator = (tokens[i*chunk_length:(i+1)*chunk_length + overlap] for i in range(num_slices))
    yield from chunks_iterator




def push_summarizations(doc_text, completion_model, max_output_tokens):
         
    for chunk in chunked_words(tokens, chunk_length=max_summ_tokens):
        print("Chunking summarization", len(chunk))
        d['summary'].append(openai_summarize(enc.decode(chunk), completion_model, max_output_tokens))
                
    summary = '\n'.join(d['summary'])
    logging.info(f"Summary {summary}")
    print(f"Summary {summary}")

    push_embeddings(summary, enc.encode(summary), lang,  timestamp, doc_id, doc_url, text_suffix = 'summ')



re_strs = [
    "customXml\/[-a-zA-Z0-9+&@#\/%=~_|$?!:,.]*", 
    "ppt\/[-a-zA-Z0-9+&@#\/%=~_|$?!:,.]*",
    "\.MsftOfcThm_[-a-zA-Z0-9+&@#\/%=~_|$?!:,.]*[\r\n\t\f\v ]\{[\r\n\t\f\v ].*[\r\n\t\f\v ]\}",
    "SlidePowerPoint",
    "PresentationPowerPoint",
    '[a-zA-Z0-9]*\.(?:gif|emf)'
    ]



def redis_search(query: str):
    redis_conn = redis_helpers.get_new_conn()
    completion_enc = openai_helpers.get_encoder(CHOSEN_COMP_MODEL)

    query_embedding = openai_helpers.get_openai_embedding(query, CHOSEN_EMB_MODEL)    
    results = redis_helpers.redis_query_embedding_index(redis_conn, query_embedding, -1, topK=NUM_TOP_MATCHES)
    
    context = ' \n'.join([f"[{t['doc_url']}] " + t['text_en'].replace('\n', ' ') for t in results])
    context = context.replace('\n', ' ')

    for re_str in re_strs:
        matches = re.findall(re_str, context, re.DOTALL)
        for m in matches: context = context.replace(m, '')

    context = completion_enc.decode(completion_enc.encode(context)[:MAX_SEARCH_TOKENS])
    
    return context


def redis_lookup(query: str):
    redis_conn = redis_helpers.get_new_conn()
    completion_enc = openai_helpers.get_encoder(CHOSEN_COMP_MODEL)
    
    query_embedding = openai_helpers.get_openai_embedding(query, CHOSEN_EMB_MODEL)    
    results = redis_helpers.redis_query_embedding_index(redis_conn, query_embedding, -1, topK=NUM_TOP_MATCHES)

    context = ' \n'.join([f"[{t['doc_url']}] " + t['text_en'].replace('\n', ' ') for t in results])
    context = context.replace('\n', ' ')
    
    for re_str in re_strs:
        matches = re.findall(re_str, context, re.DOTALL)
        for m in matches: context = context.replace(m, '')

    context = completion_enc.decode(completion_enc.encode(context)[:MAX_SEARCH_TOKENS])
    return context