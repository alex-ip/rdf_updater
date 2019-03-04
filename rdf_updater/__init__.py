'''
Class definition for RDFUpdater
Created on 25 Feb. 2019

@author: Alex Ip
'''
import logging
import os
import sys
import yaml
import requests
import json
import re
import base64
from pprint import pprint, pformat
from lxml import etree
import skosify  # contains skosify, config, and infer
from rdflib import Graph

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO) # Initial logging level for this module
logger.debug('__name__ = {}'.format(__name__))

class RDFUpdater(object):
    settings = None
    
    def __init__(self, settings_path=None, debug=False):
        
        # Initialise and set debug property
        self._debug = None
        self.debug = debug

        package_dir = os.path.dirname(os.path.abspath(__file__))
        settings_path = settings_path or os.path.join(package_dir, 'rdf_updater_settings.yml')
        self.settings = yaml.safe_load(open(settings_path))
        
        self.rdf_configs = self.settings['rdf_configs']
        
        logger.info('Reading vocab configs from GitHub')
        self.rdf_configs.update(self.get_github_settings())
        
        #logger.debug('Settings: {}'.format(pformat(self.settings)))
        
        
    def get_rdfs(self):
        def get_rdf(rdf_config):
            if rdf_config['source_type'] == 'sparql':
                url = rdf_config['sparql_endpoint']
                http_method = requests.post
                headers = {'Accept': 'application/rdf+xml',
                           'Content-Type': 'application/sparql-query',
                           'Accept-Encoding': 'UTF-8'
                           }
                params = None
                data = '''CONSTRUCT {?s ?p ?o}
WHERE {?s ?p ?o .}'''
            elif rdf_config['source_type'] == 'http_get':
                url = rdf_config['uri']
                http_method = requests.get
                if rdf_config.get('format') == 'ttl':
                    url += '/' # SISSVoc needs to have a trailing slash
                    headers = None
                    params = {'_format': 'text/turtle'}
                else:
                    headers = {'Accept': 'application/rdf+xml',
                               'Accept-Encoding': 'UTF-8'
                               }
                    params = None

                    if rdf_config.get('rdf_url'):
                        url = rdf_config.get('rdf_url')
                    else: # Special case for ODM2
                        params = {'format': 'skos'}
                data = None
            else:
                raise Exception('Bad source type for RDF')
            #logger.debug('http_method = {}, url = {}, headers = {}, params = {}, data = {}'.format(http_method, url, headers, params, data))
            logger.info('Reading RDF from {} via {}'.format(url, rdf_config['source_type']))
            response = http_method(url, headers=headers, params=params, data=data, timeout=self.settings['timeout'])
            #logger.debug('Response content: {}'.format(str(response.content)))
            assert response.status_code == 200, 'Response status code != 200'
            return(response.content).decode('utf-8') # Convert binary to UTF-8 string
                
        logger.info('Reading RDFs from sources to files')    
        
        for _rdf_name, rdf_config in self.rdf_configs.items():
            logger.info('Obtaining data for {}'.format(rdf_config['name']))
            try:
                rdf = get_rdf(rdf_config)
                rdf = re.sub('^(\<\?xml version="1.0")\s*(\?\>.*)', '\\1 encoding="UTF-8"\\2', rdf) # Add encoding if missing
                rdf = re.sub('\r\n', '\n', rdf) # Fix bad EOLs
                
                #logger.debug('rdf = {}'.format(rdf))
                logger.info('Writing RDF to file {}'.format(rdf_config['rdf_file_path']))
                rdf_directory = os.path.dirname(os.path.abspath(rdf_config['rdf_file_path']))
                if not os.path.exists(rdf_directory):
                    logger.debug('Creating directory {}'.format(rdf_directory))
                    os.makedirs(rdf_directory)
                with open(rdf_config['rdf_file_path'], 'w', encoding='utf-8') as rdf_file:
                    rdf_file.write(rdf)
            except Exception as e:
                logger.error('ERROR: RDF get from {} to file failed: {}'.format(rdf_config['source_type'], e))
                
        logger.info('Finished reading to files')
        
        
    def put_rdfs(self):
        def put_rdf(rdf_config, rdf):
            url = self.settings['triple_store_url'] + '/data'
            if rdf_config.get('format') == 'ttl': 
                headers = {'Content-Type': 'text/turtle'}
            else:
                headers = {'Content-Type': 'application/rdf+xml'}
            params = {'graph': rdf_config['uri']}
            
            logger.info('Writing RDF to {}'.format(url))
            response = requests.put(url, headers=headers, params=params, data=rdf.encode('utf-8'), timeout=self.settings['timeout'])
            #logger.debug('Response content: {}'.format(response.content))
            assert response.status_code == 200 or response.status_code == 201, 'Response status code {}  != 200 or 201: {}'.format(response.status_code, response.content)
            return(response.content)
                
        logger.info('Writing RDFs to triple-store {} from files'.format(self.settings['triple_store_url']))           
        for _rdf_name, rdf_config in self.rdf_configs.items():
            logger.info('Writing data for {}'.format(rdf_config['name']))
            try:
                logger.info('Reading RDF from {}'.format(rdf_config['rdf_file_path']))
                with open(rdf_config['rdf_file_path'], 'r', encoding='utf-8') as rdf_file:
                    rdf = rdf_file.read()
                #logger.debug('rdf = {}'.format(rdf))
                result = json.loads(put_rdf(rdf_config, rdf))
                #logger.debug('result = {}'.format(result))
                logger.info('{} triples (re)written'.format(result['tripleCount']))
            except Exception as e:
                logger.error('ERROR: RDF put from file to triple-store failed: {}'.format(e))
                
        logger.info('Finished writing to triple-store')
        
     
    def get_github_settings(self):   
        result_dict = {}
        for github_name, github_config in self.settings['git_configs'].items():
            logger.debug('Reading configurations for {}'.format(github_name))
            url = github_config['github_url'].replace('/github.com/', '/api.github.com/repos/') + '/contents/' + github_config['source_tree']
            #logger.debug(url)
            response = requests.get(url, timeout=self.settings['timeout'])
            assert response.status_code == 200, 'Response status code != 200' 
            #logger.debug('response content = {}'.format(pformat(json.loads(response.content.decode('utf-8')))))
            rdfs = {tree_dict['name']: tree_dict['download_url']
                    for tree_dict in json.loads(response.content.decode('utf-8'))
                    if tree_dict.get('name') and tree_dict.get('download_url') 
                    }
            #logger.debug('url_list = {}'.format(pformat(url_list)))
            for rdf_name, rdf_url in rdfs.items():
                try:
                    # Skip non-RDF files
                    if os.path.splitext(os.path.basename(rdf_url))[1] != '.rdf':
                        logger.debug('Skipping {}'.format(rdf_url))
                        continue
                    
                    logger.debug('Reading config from {}'.format(rdf_name))
                    response = requests.get(rdf_url, timeout=self.settings['timeout'])
                    #logger.debug('Response content: {}'.format(str(response.content)))
                    assert response.status_code == 200, 'Response status code != 200'
    
                    vocab_tree = etree.fromstring(response.content)
                    
                    # Find all collection elements
                    collection_elements = vocab_tree.findall(path='skos:Collection', namespaces=vocab_tree.nsmap)
                    if not collection_elements: #No skos:collections defined - look for resource element parents instead                      
                        logger.warning('WARNING: {} has no explicit skos:Collection elements'.format(rdf_name))
                        resource_elements = vocab_tree.findall(path='.//rdf:Description/rdf:type[@rdf:resource="http://www.w3.org/2004/02/skos/core#Collection"]', namespaces=vocab_tree.nsmap)
                        collection_elements = [resource_element.getparent() for resource_element in resource_elements]
                    
                    #logger.debug('collection_elements = {}'.format(pformat(collection_elements)))
                    
                    if len(collection_elements) == 1:
                        collection_element = collection_elements[0]
                        collection_uri = collection_element.attrib.get('{' + vocab_tree.nsmap['rdf'] + '}about')
                    else:
                        logger.warning('WARNING: {} has multiple Collection elements'.format(rdf_name))
                        #TODO: Make this work better when there are multiple collections in one RDF
                        # Find shortest URI for collection and use that for named graphs
                        # This is a bit nasty, but it works for poorly-defined subcollection schemes
                        collection_element = None
                        collection_uri = None
                        for search_collection_element in collection_elements:
                            search_collection_uri = search_collection_element.attrib.get('{' + vocab_tree.nsmap['rdf'] + '}about')
                            if (not collection_uri) or len(search_collection_uri) < len(collection_uri):
                                collection_uri = search_collection_uri
                                collection_element = search_collection_element
                        
                    label_element = collection_element.find(path = 'rdfs:label', namespaces=vocab_tree.nsmap)
                    if label_element is None:
                        label_element = collection_element.find(path = 'dcterms:title[@{http://www.w3.org/XML/1998/namespace}lang="en"]', namespaces=vocab_tree.nsmap)
                    collection_label = label_element.text
                                            
                except Exception as e:
                    logger.warning('Unable to find collection information in {}: {}'.format(rdf_url, e))
                    continue
                
                collection_dict = {'name': collection_label,
                               'uri': collection_uri,
                               'source_type': 'http_get',
                               'rdf_file_path': github_config['rdf_dir'] + '/' + rdf_name,
                               'rdf_url': rdf_url
                               }
                logger.debug('collection_dict = {}'.format(pformat(collection_dict)))
                result_dict[os.path.splitext(rdf_name)[0]] = collection_dict
        return result_dict  
    
    def skosify_rdfs(self):
        def skosify_rdf(rdf_config):
            #temp_rdf_path = os.path.splitext(rdf_path)[0] + '_tmp.rdf'
            
            # The following is a work-around for a unicode issue in rdflib
            rdf_file = open(rdf_config['rdf_file_path'], 'rb') # Note binary reading
            rdf = Graph()
            rdf.parse(rdf_file, format='xml')
            rdf_file.close()
            
            voc = skosify.skosify(rdf, label=rdf_config['name'])
             
            skosify.infer.skos_related(voc)
            skosify.infer.skos_topConcept(voc)
            skosify.infer.skos_hierarchical(voc, narrower=True)
            skosify.infer.skos_transitive(voc, narrower=True)
              
            skosify.infer.rdfs_classes(voc)
            skosify.infer.rdfs_properties(voc)
            
            rdf_file = open(rdf_config['rdf_file_path'], 'wb') # Note binary writing
            voc.serialize(destination=rdf_file, format='xml')
            rdf_file.close()

        
        logger.info('Validating RDFs from files')           
        for _rdf_name, rdf_config in self.rdf_configs.items():
            #logger.info('Validating data for {}'.format(rdf_config['name']))
            try:
                logger.info('Validating RDF from {}'.format(rdf_config['rdf_file_path']))
                #===============================================================
                # with open(rdf_config['rdf_file_path'], 'rb') as rdf_file:
                #     rdf = rdf_file.read()
                #===============================================================
                skosify_rdf(rdf_config)
            except Exception as e:
                logger.warning('RDF validation from file {} failed: {}'.format(rdf_config['rdf_file_path'], e))
                continue
            
        logger.info('Validation of RDF files completed')
    
    
    @property
    def debug(self):
        return self._debug
    
    @debug.setter
    def debug(self, debug_value):
        if self._debug != debug_value or self._debug is None:
            self._debug = debug_value
            
            if self._debug:
                logger.setLevel(logging.DEBUG)
            else:
                logger.setLevel(logging.INFO)
                
        logger.debug('Logger {} set to level {}'.format(logger.name, logger.level))

