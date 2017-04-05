"use strict";

var _ = require('underscore'),
    $ = require('jquery'),
    Backbone = require('../../shim/backbone'),
    App = require('../app');

var GeocoderModel = Backbone.Model.extend({
    defaults: {
        query: ''
    }
});

var SuggestionModel = Backbone.Model.extend({
    url: '/api/geocode/',
    idAttribute: 'magicKey',

    defaults: {
        zoom: 18,
        isBoundaryLayer: false
    },

    setMapViewToLocation: function(zoom) {
        var lat = this.get('y'),
            lng = this.get('x');
        if (lat && lng) {
            App.map.set({
                lat: lat,
                lng: lng,
                zoom: zoom || this.get('zoom')
            });
        }
    },

    select: function() {
        var data = {
            key: this.get('magicKey'),
            search: this.get('text')
        };
        return this.fetch({ data: data });
    },

    parse: function(data) {
        // Parse from API request
        if (data.length) {
            return data[0];
        }
        // Parse from initialization
        return data;
    }
});

var BoundarySuggestionModel = SuggestionModel.extend({
    idAttribute: 'id',

    defaults: {
        isBoundaryLayer: true
    },

    select: function() {
        // We don't need to make a separate request for lat/lng
        var xhr = new $.Deferred();
        return xhr.resolve();
    }
});

var GeocodeSuggestions = Backbone.Collection.extend({
    model: SuggestionModel,
    url: function() {
        return 'https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/suggest?f=json' +
               '&searchExtent=' + this.getBoundingBox();
    },

    getBoundingBox: function() {
        // Continental US
        return '-127.17,24.76,-66.53,50.4575';
    },

    parse: function(response) {
        return response.suggestions;
    }
});

var BoundarySuggestions = GeocodeSuggestions.extend({
    model: BoundarySuggestionModel,
    url: '/api/modeling/boundary-layers-search'
});

// Composite collection which merges the results from
// GeocodeSuggestions and BoundarySuggestions.
var SuggestionsCollection = Backbone.Collection.extend({
    initialize: function() {
        this.geocodeSuggestions = new GeocodeSuggestions();
        this.boundarySuggestions = new BoundarySuggestions();
    },

    fetch: function(options) {
        var xhr = new $.Deferred();

        var requests = $.when(
            this.geocodeSuggestions.fetch(options)
                .then(_.bind(this.combineSuggestions, this)),
            this.boundarySuggestions.fetch(options)
                .then(_.bind(this.combineSuggestions, this)));

        requests
            .done(xhr.resolve)
            .fail(xhr.reject);

        return xhr.promise();
    },

    combineSuggestions: function() {
        var models = [].concat(
            this.geocodeSuggestions.models,
            this.boundarySuggestions.models);
        this.set(models);
    }
});

module.exports = {
    GeocoderModel: GeocoderModel,
    SuggestionModel: SuggestionModel,
    SuggestionsCollection: SuggestionsCollection
};
